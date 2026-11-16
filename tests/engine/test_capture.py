#!/usr/bin/env python3
"""The audio capture's lifecycle, with a fake GStreamer: errors, end of stream, a missing source,
switching sources without waiting, and a pipeline slow or failing to shut down.

CaptureTests come from the reliability review (findings 1-3), CaptureRegressions from 1.0.3.
"""
import queue
import threading
import time
import unittest

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support.fakes import FakeGst, FakePipeline


class CaptureTests(unittest.TestCase):
    def capture(self, behaviour, on_audio=lambda gen, pcm: None):
        ended, started = queue.Queue(), []
        gst = FakeGst(behaviour)
        cap = engine.Capture(on_audio, on_started=started.append,
                            on_ended=lambda gen, reason: ended.put((gen, reason)), gst=gst)
        return cap, gst, ended, started

    def test_device_error_is_reported_and_cleaned_up(self):
        """Finding 3: a GStreamer error ends the capture, shuts the pipeline down and is reported."""
        cap, gst, ended, started = self.capture('error')
        cap.start('dev', 7)
        self.assertEqual(ended.get(timeout=2), (7, 'device disappeared'))
        self.assertEqual(gst.pipelines[0].state, 'null')
        self.assertEqual(started, [7])

    def test_end_of_stream_ends_capture_without_spinning(self):
        """Finding 3: an immediate end of stream is detected instead of looping forever."""
        cap, gst, ended, _ = self.capture('eos')
        cap.start('dev', 1)
        self.assertEqual(ended.get(timeout=2), (1, 'the audio stream ended'))
        self.assertLess(gst.pipelines[0].pulls, 5)
        self.assertEqual(gst.pipelines[0].state, 'null')

    def test_exception_in_callback_is_reported_and_cleaned_up(self):
        """Finding 2: a failing consumer ends the capture visibly instead of killing it silently."""
        def boom(gen, pcm):
            raise RuntimeError('injected consumer failure')
        cap, gst, ended, _ = self.capture('ok', on_audio=boom)
        cap.start('dev', 3)
        gen, reason = ended.get(timeout=2)
        self.assertEqual(gen, 3)
        self.assertIn('injected consumer failure', reason)
        self.assertEqual(gst.pipelines[0].state, 'null')

    def test_missing_source_is_reported_not_replaced_by_the_default(self):
        cap, gst, ended, started = self.capture('missing')
        cap.start('headphones.monitor', 4)
        gen, reason = ended.get(timeout=2)
        self.assertEqual(gen, 4)
        self.assertIn('headphones.monitor was not found', reason)
        self.assertEqual(started, [])  # no audio from the substitute device was ever delivered
        self.assertEqual(gst.pipelines[0].state, 'null')

    def test_refused_start_is_reported_and_leaves_nothing_running(self):
        cap, gst, ended, _ = self.capture('refuse')
        cap.start('dev', 1)
        self.assertIn('could not start capturing', ended.get(timeout=2)[1])
        self.assertTrue(cap.wait_stopped())
        self.assertEqual(gst.pipelines[0].state, 'null')
        self.assertIsNone(cap.current)

    def test_restart_does_not_wait_and_old_capture_stops_quietly(self):
        """Finding 1: switching sources never blocks, stops the old pipeline, and a requested
        stop is not reported as a failure."""
        cap, gst, ended, _ = self.capture('ok')
        cap.start('desktop', 1)
        end = time.monotonic() + 2
        while not gst.pipelines and time.monotonic() < end:
            time.sleep(0.002)
        self.assertTrue(gst.pipelines)
        t0 = time.monotonic()
        cap.start('mic', 2)
        self.assertLess(time.monotonic() - t0, 0.1)
        cap.stop()
        self.assertTrue(cap.wait_stopped())
        self.assertEqual(gst.pipelines[0].state, 'null')
        self.assertTrue(ended.empty())


class CaptureRegressions(unittest.TestCase):
    def test_failed_teardown_is_retained_and_retried_before_a_replacement(self):
        started, ended = queue.Queue(), queue.Queue()
        fail = threading.Event()
        class Pipeline(FakePipeline):
            def set_state(self, state):
                if state == 'null' and self.device == 'desktop' and fail.is_set():
                    return 'failure'
                return super().set_state(state)
        class Gst(FakeGst):
            def parse_launch(self, description):
                p = Pipeline('ok')
                self.pipelines.append(p)
                return p
        gst = Gst('ok')
        cap = engine.Capture(lambda *a: None, on_started=started.put,
                            on_ended=lambda gen, why: ended.put((gen, why)), gst=gst)
        try:
            cap.start('desktop', 1)
            self.assertEqual(started.get(timeout=2), 1)
            fail.set()
            cap.start('mic', 2)
            gen, reason = ended.get(timeout=2)
            self.assertEqual(gen, 2)
            self.assertIn('could not shut down', reason)
            self.assertEqual(len(gst.pipelines), 1)
            self.assertIs(cap._stranded, gst.pipelines[0])
            fail.clear()
            cap.start('mic', 3)
            self.assertEqual(started.get(timeout=2), 3)
            self.assertEqual(gst.pipelines[0].state, 'null')
            self.assertIsNone(cap._stranded)
        finally:
            fail.clear()
            cap.stop()
            self.assertTrue(cap.wait_stopped(3))

    def test_slow_shutdown_does_not_block_actions_or_overlap_replacements(self):
        entered, release = threading.Event(), threading.Event()
        calls, started, ended = [], queue.Queue(), queue.Queue()
        class Pipeline(FakePipeline):
            def set_state(self, state):
                calls.append((threading.current_thread().name, state))
                if state == 'null' and self.device == 'desktop':
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError('test did not release native shutdown')
                return super().set_state(state)
        class Gst(FakeGst):
            def parse_launch(self, description):
                p = Pipeline('ok')
                self.pipelines.append(p)
                return p
        gst = Gst('ok')
        cap = engine.Capture(lambda *a: None, on_started=started.put, on_ended=lambda *a: ended.put(a), gst=gst)
        try:
            cap.start('desktop', 1)
            self.assertEqual(started.get(timeout=2), 1)
            cap.start('mic', 2)
            self.assertTrue(entered.wait(1))
            returned = threading.Event()
            action = threading.Thread(target=lambda: (cap.start('newest', 3), returned.set()), name='window-action')
            action.start()
            self.assertTrue(returned.wait(0.3), 'source action waited for native shutdown')
            action.join(1)
            self.assertEqual(len(gst.pipelines), 1)
            release.set()
            self.assertEqual(started.get(timeout=2), 3)
            self.assertEqual(len(gst.pipelines), 2)
        finally:
            release.set()
            cap.stop()
            self.assertTrue(cap.wait_stopped(3))
        self.assertEqual({name for name, _ in calls}, {'nowcaption-capture'})
        self.assertTrue(ended.empty())

    def test_stop_during_slow_pipeline_setup_cancels_playing(self):
        entered, release = threading.Event(), threading.Event()
        class Gst(FakeGst):
            def parse_launch(self, description):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError('test did not release pipeline setup')
                return super().parse_launch(description)
        gst, started = Gst('ok'), []
        cap = engine.Capture(lambda *a: None, on_started=started.append, gst=gst)
        try:
            cap.start('desktop', 1)
            self.assertTrue(entered.wait(1))
            cap.stop()
        finally:
            release.set()
            self.assertTrue(cap.wait_stopped(3))
        self.assertEqual(started, [])
        self.assertEqual(gst.pipelines[0].state, 'null')


if __name__ == '__main__':
    unittest.main(verbosity=2)
