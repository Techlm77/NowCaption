#!/usr/bin/env python3
"""Listening's recovery: failed recognition backs off and retries, and an error or retry from an
older source or stream can never undo a newer one. The real worker thread and the application's
own methods (support/app_loader.py), with controlled models and timers; no GTK.

RecoveryTests come from 1.0.4, RecoveryGenerationTests from 1.1.5.
"""
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support.app_loader import APP, app_with_fakes
from support.fakes import SILENCE, SPEECH, Events, FakeRecognizer, FakeStream, Timers


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.timers = Timers()
        self.patch = patch.dict(APP, GLib=self.timers)
        self.patch.start()
        self.app = app_with_fakes()
        self.app.listening.ready = True
        self.events = Events()

    def tearDown(self):
        if getattr(self, 'worker', None):
            self.worker.stop()
            self.worker.join(3)
            self.assertFalse(self.worker.is_alive())
        self.patch.stop()

    def start_worker(self, recognizer):
        self.worker = engine.RecognitionWorker(self.events, load=lambda: (recognizer, None))
        self.app.listening.worker = self.worker
        self.worker.start()
        self.events.wait('ready')

    def barrier(self):
        """Deliver every event before an ordered snapshot on the simulated UI thread."""
        self.worker.snapshot(lambda text: None)
        end = time.monotonic() + 3
        while True:
            kind, value = self.events.q.get(timeout=max(0.001, end - time.monotonic()))
            if kind == 'snapshot':
                return value[1]
            self.app.listening.on_worker(kind, value)

    def test_consecutive_decode_failures_back_off_then_stop_after_three_retries(self):
        rec = FakeRecognizer()
        rec.fail = True
        self.start_worker(rec)
        for failure in range(1, 5):
            self.worker.feed(0, SPEECH)
            self.barrier()
            self.assertEqual(self.app.listening.recognition_failures, failure)
            if failure <= 3:
                self.timers.fire(self.app.listening.recognition_retry_id)
                self.barrier()  # replaying empty captions must not count as recognition success
                self.assertEqual(self.app.listening.recognition_failures, failure)
        self.assertEqual(self.timers.delays, [2, 4, 8])
        self.assertEqual(self.timers.pending, {})
        self.assertIn('press Restart', self.app.window.statuses[-1])

    def test_replayed_old_words_do_not_reset_the_failure_count(self):
        rec = FakeRecognizer(['already recognised '])
        self.start_worker(rec)
        self.worker.feed(0, SPEECH)
        self.barrier()
        rec.fail = True
        self.worker.feed(0, SPEECH)
        self.barrier()
        self.timers.fire(self.app.listening.recognition_retry_id)
        self.barrier()
        self.assertEqual(self.app.listening.recognition_failures, 1)
        self.assertIn('Already recognised', self.app.window.text)

    def test_buffering_before_a_permanent_decode_failure_does_not_reset_backoff(self):
        class Stream(FakeStream):
            def __init__(self, rec):
                super().__init__(rec)
                self.packets = 0
            def accept_waveform(self, rate, samples):
                super().accept_waveform(rate, samples)
                self.packets += 1
        class Rec(FakeRecognizer):
            def create_stream(self):
                return Stream(self)
            def is_ready(self, stream):
                return stream.packets >= 3
            def decode_stream(self, stream):
                raise RuntimeError('permanent inference failure')
        self.start_worker(Rec())
        for failure in range(1, 5):
            for _ in range(2):
                self.worker.feed(0, SPEECH)
                self.barrier()
                self.assertEqual(self.app.listening.recognition_failures, failure - 1)
            self.worker.feed(0, SPEECH)
            self.barrier()
            self.assertEqual(self.app.listening.recognition_failures, failure)
            if failure <= 3:
                self.timers.fire(self.app.listening.recognition_retry_id)
                self.barrier()
        self.assertEqual(self.timers.delays, [2, 4, 8])
        self.assertEqual(self.timers.pending, {})

    def test_actual_success_resets_backoff_even_without_new_words(self):
        rec = FakeRecognizer()
        rec.fail = True
        self.start_worker(rec)
        self.worker.feed(0, SPEECH)
        self.barrier()
        self.assertEqual(self.app.listening.recognition_failures, 1)
        rec.fail = False
        self.timers.fire(self.app.listening.recognition_retry_id)
        self.barrier()
        self.assertEqual(self.app.listening.recognition_failures, 1)
        self.worker.feed(0, SILENCE)
        self.barrier()
        self.assertEqual(self.app.listening.recognition_failures, 0)
        self.assertEqual(self.timers.pending, {})

    def test_manual_source_change_invalidates_an_already_dispatched_timer(self):
        rec = FakeRecognizer()
        rec.fail = True
        self.start_worker(rec)
        self.worker.feed(0, SPEECH)
        self.barrier()
        old_timer = self.app.listening.recognition_retry_id
        old_callback = self.timers.pending[old_timer]
        rec.fail = False
        self.app.listening.on_source(NS(set_state=lambda value: None), NS(get_string=lambda: 'mic'))
        self.barrier()
        recovered_stream = self.worker.captioner.stream
        self.assertNotIn(old_timer, self.timers.pending)
        old_callback()  # cancellation cannot recall a callback the UI already dispatched
        self.barrier()
        self.assertIs(self.worker.captioner.stream, recovered_stream)

    def test_clear_and_shutdown_cancel_pending_recognition_timers(self):
        self.app.listening.worker = NS(clear=lambda gen: None, set_source=lambda gen: None,
                             stop=lambda: None, restart=lambda **kwargs: None)
        self.app.listening.on_worker('error', (self.app.listening.source_gen, 0, 'failure'))
        old = self.app.listening.recognition_retry_id
        self.app.listening.clear_transcript()
        self.assertNotIn(old, self.timers.pending)
        self.app.listening.on_worker('error', (self.app.listening.source_gen, 0, 'another failure'))
        old = self.app.listening.recognition_retry_id
        self.app.on_shutdown(None)
        self.assertNotIn(old, self.timers.pending)

    def test_stale_success_from_an_old_source_cannot_reset_backoff(self):
        self.app.listening.source_gen, self.app.listening.recognition_failures = 3, 2
        self.app.listening.on_worker('recognition-ok', 2)
        self.assertEqual(self.app.listening.recognition_failures, 2)

    def test_queued_error_is_ignored_after_source_recovery_has_already_succeeded(self):
        self.app.listening.worker = NS(broken=False)
        self.app.listening.on_worker('error', (self.app.listening.source_gen, 0, 'an error from before the source change'))
        self.assertEqual(self.app.listening.recognition_failures, 0)
        self.assertEqual(self.timers.pending, {})

    def test_queued_automatic_retry_cannot_replace_a_healthy_buffered_stream(self):
        class Stream(FakeStream):
            def __init__(self, rec):
                super().__init__(rec)
                self.pending = ''

            def accept_waveform(self, rate, samples):
                if np.any(samples) and self.rec.script:
                    self.pending += self.rec.script.pop(0)
                else:
                    self.text += self.pending
                    self.pending = ''
                self.ready = True  # like the real model, the text changes only in a decode

        class Rec(FakeRecognizer):
            def create_stream(self):
                return Stream(self)

        self.start_worker(Rec(['buffered speech ']))
        self.worker.feed(0, SPEECH)
        self.barrier()
        recovered = self.worker.captioner.stream
        self.assertEqual(recovered.pending, 'buffered speech ')
        self.worker.restart(recover_only=True, source_gen=0)
        self.barrier()
        self.assertIs(self.worker.captioner.stream, recovered)
        self.worker.feed(0, SILENCE)
        self.assertIn('Buffered speech', self.barrier())

    def test_retry_for_a_previous_source_is_ignored_even_if_new_source_is_broken(self):
        rec = FakeRecognizer()
        rec.fail = True
        self.start_worker(rec)
        self.worker.set_source(1)
        self.worker.feed(1, SPEECH)
        self.barrier()
        failed_stream = self.worker.captioner.stream
        self.worker.restart(recover_only=True, source_gen=0)
        self.barrier()
        self.assertIs(self.worker.captioner.stream, failed_stream)
        self.assertTrue(self.worker.broken)


class RecoveryGenerationTests(unittest.TestCase):
    def setUp(self):
        self.timers = Timers()
        patcher = patch.dict(APP, GLib=self.timers)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = app_with_fakes()
        self.app.listening.ready = True

    def start_worker(self, rec):
        events = Events()
        worker = engine.RecognitionWorker(events, load=lambda: (rec, None))
        self.app.listening.worker = worker
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        return worker, events

    def test_old_source_error_is_ignored_even_when_the_new_source_also_failed(self):
        rec = FakeRecognizer()
        rec.fail = True
        worker, events = self.start_worker(rec)
        worker.feed(0, SPEECH)
        old = events.wait('error')
        self.app.listening.on_source(NS(set_state=lambda value: None), NS(get_string=lambda: 'mic'))
        worker.feed(self.app.listening.source_gen, SPEECH)
        current = events.wait('error')
        self.app.listening.on_worker('error', old)
        self.assertEqual(self.app.listening.recognition_failures, 0)
        self.app.listening.on_worker('error', current)
        self.assertEqual(self.app.listening.recognition_failures, 1)
        self.assertEqual(self.timers.delays, [2])

    def test_switching_sources_during_a_decode_does_not_relabel_its_error(self):
        entered, release = threading.Event(), threading.Event()

        class BlockingStream(FakeStream):
            def accept_waveform(self, rate, samples):
                if not entered.is_set():
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('test did not release the old decode')
                return super().accept_waveform(rate, samples)

        class Rec(FakeRecognizer):
            def create_stream(self):
                return BlockingStream(self)

        rec = Rec()
        rec.fail = True
        worker, events = self.start_worker(rec)
        self.addCleanup(release.set)
        worker.feed(0, SPEECH)
        self.assertTrue(entered.wait(2))
        self.app.listening.on_source(NS(set_state=lambda value: None), NS(get_string=lambda: 'mic'))
        worker.feed(self.app.listening.source_gen, SPEECH)
        release.set()
        old, current = events.wait('error'), events.wait('error')
        self.assertEqual(old[0], 0)
        self.assertEqual(current[0], self.app.listening.source_gen)
        self.app.listening.on_worker('error', old)
        self.app.listening.on_worker('error', current)
        self.assertEqual(self.app.listening.recognition_failures, 1)

    def test_old_stream_error_is_ignored_after_a_retry_on_the_same_source(self):
        rec = FakeRecognizer()
        rec.fail = True
        worker, events = self.start_worker(rec)
        worker.feed(0, SPEECH)
        old = events.wait('error')
        worker.restart()
        worker.feed(0, SPEECH)
        current = events.wait('error')
        self.assertNotEqual(old[1], current[1])
        self.app.listening.on_worker('error', old)
        self.app.listening.on_worker('error', current)
        self.assertEqual(self.timers.delays, [2])

    def test_obsolete_error_cannot_cancel_a_current_retry_timer(self):
        self.app.listening.source_gen = 2
        self.app.listening.worker = NS(broken=True, stream_gen=4)
        self.app.listening.on_worker('error', (2, 4, 'current failure'))
        timer = self.app.listening.recognition_retry_id
        self.app.listening.on_worker('error', (1, 3, 'old failure'))
        self.assertEqual(self.app.listening.recognition_retry_id, timer)
        self.assertIn(timer, self.timers.pending)
        self.assertEqual(self.app.listening.recognition_failures, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
