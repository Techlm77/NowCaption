#!/usr/bin/env python3
"""The recognition worker, the one thread that touches the speech models: sources, Clear,
failures and recovery, commands and snapshots, and audio arriving faster than it is recognised.
Fake models (support/fakes.py); no GTK, audio server or downloaded models.

WorkerTests come from the reliability review (findings 1-6), WorkerRegressions from 1.0.3,
WorkerCommandTests from 1.1.2 and SustainedOverloadTests from 1.1.6.
"""
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support import fakes as fixtures
from support.app_loader import app_with_fakes
from support.fakes import SILENCE, SPEECH, Events, FakePunct, FakeRecognizer, FakeStream

BLOCK = np.full(640, 1000, dtype=np.int16).tobytes()  # 40 ms, as the capture delivers it


def make_worker(rec, punct=None):
    events = Events()
    worker = engine.RecognitionWorker(events, load=lambda: (rec, punct))
    worker.start()
    events.wait('ready')
    worker.set_source(1)
    return worker, events


class WorkerTests(unittest.TestCase):
    def tearDown(self):
        if getattr(self, 'worker', None):
            self.worker.stop()
            self.worker.join(2)

    def test_only_the_worker_thread_touches_the_stream(self):
        """Finding 1: a source change while audio is being recognised never uses the stream
        from two threads, and audio from the old source is not recognised afterwards."""
        rec = FakeRecognizer(['a ', 'b ', 'c ', 'd '] * 50)
        rec.slow = 0.005
        self.worker, events = make_worker(rec)
        feeders = [threading.Thread(target=lambda g=g: [self.worker.feed(g, SPEECH) for _ in range(30)])
                   for g in (1, 1, 2)]
        for t in feeders[:2]:
            t.start()
        self.worker.set_source(2)  # what the GTK thread does on a source switch
        feeders[2].start()
        for t in feeders:
            t.join()
        events.snapshot(self.worker)  # waits until queued work is processed
        self.assertEqual(rec.threads, {'nowcaption-recognition'})
        self.assertEqual(rec.max_active, 1)

    def test_stale_source_audio_is_ignored(self):
        rec = FakeRecognizer(['old ', 'new '])
        self.worker, events = make_worker(rec)
        self.worker.set_source(2)
        self.worker.feed(1, SPEECH)  # a capture thread that has not stopped yet
        self.worker.feed(2, SPEECH)
        self.assertEqual(events.snapshot(self.worker).split('] ', 1)[-1], 'Old')

    def test_recognizer_exception_is_reported_and_recoverable(self):
        """Finding 2: an exception is reported, the worker survives, and restart() resumes
        captioning while keeping the transcript."""
        rec = FakeRecognizer(['hello ', 'again '])
        self.worker, events = make_worker(rec)
        self.worker.feed(1, SPEECH)
        self.worker.feed(1, SILENCE)
        events.wait('caption', lambda v: 'Hello' in v[1])
        rec.fail = True
        self.worker.feed(1, SPEECH)
        self.assertIn('injected recognizer failure', events.wait('error')[2])
        self.assertTrue(self.worker.is_alive())
        rec.fail = False
        self.worker.restart()
        self.worker.feed(1, SPEECH)
        events.wait('caption', lambda v: 'again' in v[1].lower())
        text = events.snapshot(self.worker).lower()
        self.assertIn('hello', text)
        self.assertIn('again', text)

    def test_punctuation_failure_falls_back_to_plain_captions(self):
        rec = FakeRecognizer(['hello ', 'world '])
        self.worker, events = make_worker(rec, FakePunct(fail=True))
        self.worker.feed(1, SPEECH)
        self.assertIn('Punctuation turned off', events.wait('warning'))
        self.worker.feed(1, SPEECH)
        events.wait('caption', lambda v: v[1] == 'Hello world')

    def test_transcript_includes_unfinished_words_once(self):
        """Finding 4: copy/save contain the words on screen, exactly once."""
        rec = FakeRecognizer(['hello ', 'there '])
        self.worker, events = make_worker(rec)
        self.worker.feed(1, SPEECH)
        self.worker.feed(1, SPEECH)
        events.wait('caption', lambda v: v[1] == 'Hello there')
        text = events.snapshot(self.worker)
        self.assertEqual(text.count('Hello there'), 1, text)

    def test_clear_does_not_bring_words_back(self):
        """Finding 5: words spoken before Clear never reappear, and captions queued before the
        clear carry the old generation so the window drops them."""
        rec = FakeRecognizer(['private ', 'words ', 'and ', 'more '])
        self.worker, events = make_worker(rec)
        self.worker.feed(1, SPEECH)
        self.worker.feed(1, SPEECH)
        events.wait('caption', lambda v: v[1] == 'Private words')
        self.worker.clear(1)
        events.wait('caption', lambda v: v == (1, ''))
        self.worker.feed(1, SPEECH)
        self.worker.feed(1, SPEECH)
        gen, text = events.wait('caption', lambda v: 'more' in v[1].lower())
        self.assertEqual((gen, text), (1, 'And more'))
        self.assertNotIn('rivate', events.snapshot(self.worker))

    def test_transcript_keeps_the_whole_session(self):
        """Finding 6: more than 500 finished lines are all exported."""
        rec = FakeRecognizer([f'segment{i:04d} ' for i in range(600)])
        self.worker, events = make_worker(rec)
        for _ in range(600):
            self.worker.feed(1, SPEECH)
            self.worker.feed(1, SILENCE)
            self.worker.feed(1, SILENCE * 12)  # > SILENCE_FLUSH_S: commits the line
            events.snapshot(self.worker)  # stay near real time, as live audio does, so nothing is skipped
        text = events.snapshot(self.worker)
        self.assertIn('segment0000', text.lower())
        self.assertIn('segment0599', text.lower())

    def test_model_load_failure_is_reported_and_retried(self):
        attempts = []

        def load():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError('model missing')
            return FakeRecognizer(['ok ']), None

        events = Events()
        self.worker = engine.RecognitionWorker(events, load=load)
        self.worker.start()
        self.assertIn('model missing', events.wait('load-error'))
        self.worker.set_source(1)  # the user picks a sound source again
        events.wait('ready')
        self.assertEqual(len(attempts), 2)

    def test_overload_drops_old_audio_and_says_so(self):
        rec = FakeRecognizer(['x '] * 1000)
        rec.slow = 0.02
        self.worker, events = make_worker(rec)
        for _ in range(engine.RecognitionWorker.MAX_BACKLOG * 3):
            self.worker.feed(1, SPEECH)
        self.assertGreater(events.wait('overload'), 0)

    def test_overload_reports_the_seconds_of_audio_actually_dropped(self):
        reports = []
        worker = engine.RecognitionWorker(lambda kind, value: reports.append((kind, value)))  # not started
        block = np.full(640, 1000, dtype=np.int16).tobytes()  # 40 ms, as the capture delivers it
        for _ in range(2 * worker.MAX_BACKLOG):
            worker.feed(0, block)
        self.assertEqual(worker.backlog, worker.MAX_BACKLOG)
        self.assertEqual(len(reports), 1)
        kind, seconds = reports[0]
        self.assertEqual(kind, 'overload')
        self.assertAlmostEqual(seconds, worker.MAX_BACKLOG * 0.04)  # the whole backlog, skipped at once


class WorkerRegressions(unittest.TestCase):
    def tearDown(self):
        if getattr(self, 'worker', None):
            self.worker.stop()
            self.worker.join(3)
            self.assertFalse(self.worker.is_alive())

    def make_worker(self, rec, load=None):
        events = Events()
        self.worker = engine.RecognitionWorker(events, load=load or (lambda: (rec, None)))
        self.worker.start()
        return events

    def test_source_menu_retries_model_load_and_captioning_resumes(self):
        attempts = []
        def load():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError('temporary loading failure')
            return FakeRecognizer(['recovered ']), None
        events = self.make_worker(None, load)
        a = app_with_fakes()
        a.listening.worker = self.worker
        a.listening.on_worker('load-error', events.wait('load-error'))
        a.listening.on_source(NS(set_state=lambda v: None), NS(get_string=lambda: 'mic'))
        a.listening.on_worker('ready', events.wait('ready'))
        self.worker.feed(a.listening.source_gen, SPEECH)
        a.listening.on_worker('caption', events.wait('caption', lambda v: 'Recovered' in v[1]))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(a.listening.capture.starts, [(engine.MICROPHONE, 1)])
        self.assertEqual(a.window.text, 'Recovered')

    def test_initial_stream_creation_failure_is_reported_and_retryable(self):
        class Rec(FakeRecognizer):
            attempts = 0
            def create_stream(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError('stream allocation failed')
                return super().create_stream()
        events = self.make_worker(Rec(['ok ']))
        self.assertIn('stream allocation failed', events.wait('load-error'))
        self.assertTrue(self.worker.is_alive())
        self.worker.restart()
        events.wait('ready')
        self.worker.feed(0, SPEECH)
        self.assertIn('Ok', events.wait('caption', lambda v: 'Ok' in v[1])[1])

    def test_recovery_preserves_unfinished_words_once_and_their_timestamp(self):
        rec = FakeRecognizer(['unfinished ', 'later '])
        events = self.make_worker(rec)
        events.wait('ready')
        self.worker.feed(0, SPEECH)
        events.wait('caption', lambda v: 'Unfinished' in v[1])
        before = events.snapshot(self.worker)
        rec.fail = True
        self.worker.feed(0, SPEECH)
        events.wait('error')
        rec.fail = False
        self.worker.restart()
        self.worker.feed(0, SPEECH)
        after = events.snapshot(self.worker)
        self.assertIn(before, after)
        self.assertEqual(after.lower().count('unfinished'), 1)
        self.assertIn('later', after.lower())

    def test_clear_while_models_unavailable_preserves_caption_generation(self):
        attempts = []
        def load():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError('temporary loading failure')
            return FakeRecognizer(['new ']), None
        events = self.make_worker(None, load)
        events.wait('load-error')
        self.worker.clear(7)
        self.worker.restart()
        events.wait('ready')
        self.worker.feed(0, SPEECH)
        self.assertEqual(events.wait('caption', lambda v: 'New' in v[1]), (7, 'New'))

    def test_clear_discards_words_still_buffered_inside_the_old_model(self):
        class Stream(FakeStream):
            pending = ''
            def accept_waveform(self, rate, samples):
                if np.any(samples):
                    self.pending += self.rec.script.pop(0)
                else:
                    self.text += self.pending
                    self.pending = ''
                self.ready = True  # like the real model, the text changes only in a decode
        class Rec(FakeRecognizer):
            def create_stream(self):
                return Stream(self)
        cap = engine.Captioner(Rec(['private ', 'new ']))
        cap.feed(SPEECH)
        cap.clear()
        cap.feed(SILENCE)
        self.assertEqual(cap.transcript(), '')
        cap.feed(SPEECH)
        cap.feed(SILENCE)
        self.assertIn('New', cap.transcript())
        self.assertNotIn('private', cap.transcript().lower())

    def test_clear_restarts_capture_to_discard_its_old_pending_audio(self):
        a = app_with_fakes()
        calls = []
        a.listening.worker = NS(clear=lambda gen: calls.append(('clear', gen)),
                      set_source=lambda gen: calls.append(('source', gen)))
        a.listening.ready = True
        a.window.text = 'old words'
        a.listening.clear_transcript()
        self.assertEqual(calls, [('clear', 1), ('source', 1)])
        self.assertEqual(a.listening.capture.starts, [(engine.DESKTOP, 1)])
        self.assertEqual(a.window.text, '')

    def test_stalled_recognizer_keeps_audio_bounded_and_reports_overload(self):
        entered, release = threading.Event(), threading.Event()
        class Stream(FakeStream):
            def accept_waveform(self, rate, samples):
                if np.any(samples):
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError('test did not release the recognizer')
                super().accept_waveform(rate, samples)
        class Rec(FakeRecognizer):
            def create_stream(self):
                return Stream(self)
        events = self.make_worker(Rec(['word '] * 100))
        events.wait('ready')
        self.worker.feed(0, SPEECH)
        self.assertTrue(entered.wait(1))
        try:
            for _ in range(1000):
                self.worker.feed(0, bytes(bytearray(SPEECH)))
            with self.worker.lock:
                self.assertLessEqual(self.worker.backlog, self.worker.MAX_BACKLOG)
                self.assertLessEqual(len(self.worker.inbox), self.worker.MAX_BACKLOG)
            self.assertGreater(events.wait('overload'), 0)
            self.worker.clear(9)
            self.worker.snapshot(None)
            with self.worker.lock:
                self.assertEqual(self.worker.backlog, 0)
                self.assertEqual([k for k, _ in self.worker.inbox], ['clear', 'snapshot'])
        finally:
            release.set()
        events.wait('caption', lambda v: v == (9, ''))
        self.assertEqual(events.wait('snapshot')[1], '')

    def test_shutdown_never_joins_a_stalled_worker_on_the_window_thread(self):
        a = app_with_fakes()
        calls = []
        def forbidden_join(*a, **kw):
            self.fail('window shutdown must not join the native worker')
        a.listening.worker = NS(stop=lambda: calls.append('stop'), join=forbidden_join)
        a.on_shutdown(None)
        self.assertTrue(a.closing)
        self.assertEqual(calls, ['stop'])


class WorkerCommandTests(unittest.TestCase):
    def start_worker(self, script=('hello ', 'again '), punct=None):
        events = fixtures.Events()
        worker = engine.RecognitionWorker(events, load=lambda: (fixtures.FakeRecognizer(script), punct))
        worker.start()
        self.addCleanup(self.stop_worker, worker)
        events.wait('ready')
        return worker, events

    def stop_worker(self, worker):
        worker.stop()
        worker.join(3)
        self.assertFalse(worker.is_alive())

    def assert_healthy(self, worker, events):
        self.assertTrue(worker.is_alive())
        self.assertFalse(worker.broken)
        self.assertFalse(any(kind == 'error' for kind, _ in events.seen))

    def test_an_unexpected_command_error_is_reported_and_worker_can_recover(self):
        worker, events = self.start_worker()
        handle = worker._handle
        def fail(kind, value):
            if kind == 'restart':
                raise RuntimeError('command failed')
            handle(kind, value)
        with patch.object(worker, '_handle', side_effect=fail):
            worker.restart()
            self.assertIn('command failed', events.wait('error')[2])
        self.assertTrue(worker.is_alive())
        worker.restart()
        worker.feed(0, fixtures.SPEECH)
        self.assertIn('Hello', events.snapshot(worker))
        self.assertFalse(worker.broken)

    def test_snapshot_failure_replies_with_error_without_breaking_recognition(self):
        worker, events = self.start_worker()
        errors = []
        with patch.object(worker.captioner, 'transcript', side_effect=RuntimeError('export failed')):
            worker.snapshot(lambda text: self.fail('failed snapshot reported success'), on_error=errors.append)
            callback, error = events.wait('snapshot-error')
            callback(error)
        self.assertEqual(errors, ['export failed'])
        worker.feed(0, fixtures.SPEECH)
        self.assertIn('Hello', events.snapshot(worker))
        self.assert_healthy(worker, events)

    def test_snapshot_of_a_stopped_worker_is_rejected_instead_of_lost(self):
        worker, events = self.start_worker()
        self.stop_worker(worker)
        with self.assertRaisesRegex(RuntimeError, 'not running'):
            worker.snapshot(None)

    def test_frozen_punctuation_survives_source_changes_and_recovery(self):
        worker, events = self.start_worker(
            script=[f'line{i} a b c ' for i in range(7)],
            punct=fixtures.FlakyPunctuation())
        for _ in range(7):
            worker.feed(0, fixtures.SPEECH)
            worker.feed(0, fixtures.SILENCE * 12)
            events.snapshot(worker)
        before = [value[1] for kind, value in events.seen if kind == 'caption'][-1]
        for command in (lambda: worker.set_source(1), worker.restart):
            command()
            events.snapshot(worker)
            after = [value[1] for kind, value in events.seen if kind == 'caption'][-1]
            self.assertEqual(before, after)


class SustainedOverloadTests(unittest.TestCase):
    def tearDown(self):
        if getattr(self, 'worker', None):
            self.worker.stop()

    def test_a_full_backlog_is_skipped_at_once_and_reported_once(self):
        reports = []
        worker = engine.RecognitionWorker(lambda kind, value: reports.append((kind, value)))  # not started
        for _ in range(worker.MAX_BACKLOG + 1):
            worker.feed(0, BLOCK)
        self.assertEqual(worker.backlog, 1, 'recognition should go on from the newest audio')
        self.assertEqual(len(reports), 1)
        kind, seconds = reports[0]
        self.assertEqual(kind, 'overload')
        self.assertAlmostEqual(seconds, worker.MAX_BACKLOG * 0.04)

    def test_half_real_time_recognition_restarts_once_per_catch_up_not_per_block(self):
        # Two capture blocks arrive while each one is recognised, as on a CPU that is too slow.
        # 1.1.5 dropped one block at a time: captions stayed 2 s late, the stream restarted at
        # almost every block, and each restart was announced as "skipped 0.0 s of audio".
        total, fed, all_fed = 1000, [1], threading.Event()
        worker_box = []

        class Stream(FakeStream):
            def accept_waveform(self, rate, samples):
                super().accept_waveform(rate, samples)
                if np.any(samples):
                    for _ in range(2):
                        if fed[0] < total:
                            fed[0] += 1
                            worker_box[0].feed(1, BLOCK)
                    if fed[0] >= total:
                        all_fed.set()

        class Rec(FakeRecognizer):
            def create_stream(self):
                return Stream(self)

        events = Events()
        self.worker = engine.RecognitionWorker(events, load=lambda: (Rec(['w '] * 5000), None))
        worker_box.append(self.worker)
        self.worker.start()
        events.wait('ready')
        self.worker.set_source(1)
        events.snapshot(self.worker)  # the source change has been handled
        restarts_before = self.worker.stream_gen
        self.worker.feed(1, BLOCK)
        self.assertTrue(all_fed.wait(10))
        events.snapshot(self.worker)  # every block fed before this was recognised or skipped
        restarts = self.worker.stream_gen - restarts_before
        overloads = [v for k, v in events.seen if k == 'overload']
        self.assertTrue(overloads, 'falling behind was never reported')
        self.assertLessEqual(len(overloads), total // self.worker.MAX_BACKLOG)
        self.assertLessEqual(restarts, len(overloads))
        self.assertGreaterEqual(min(overloads), 1.0, 'a notice would read "skipped 0.0 s"')


class RecogniseTests(unittest.TestCase):
    """What the worker does with each block of audio it takes from its queue (_recognise): after
    dropped audio it goes on in a new stream, a block taken just as the source changes is not
    recognised, and a broken stream is never fed nor replaced until restart(). Without these
    tests, removing any of those checks failed no test (mutation testing, November 2026)."""

    def tearDown(self):
        if getattr(self, 'worker', None):
            self.worker.stop()
            self.worker.join(2)

    def recognizer(self, script, fail_from=None):
        """A FakeRecognizer that keeps its streams, and cannot make the fail_from'th one or later."""
        streams = []

        class Rec(FakeRecognizer):
            def create_stream(self):
                if fail_from is not None and len(streams) + 1 >= fail_from:
                    raise RuntimeError('injected stream failure')
                streams.append(super().create_stream())
                return streams[-1]
        return Rec(script), streams

    def start(self, rec, on_event=None):
        events = Events()
        self.worker = engine.RecognitionWorker(on_event or events, load=lambda: (rec, None))
        return events

    def overflow(self, events):
        """Feed more audio than the queue holds: all but the last block is dropped."""
        for _ in range(self.worker.MAX_BACKLOG + 1):
            self.worker.feed(0, BLOCK)
        self.assertGreater(events.wait('overload'), 0)

    def test_after_dropped_audio_the_next_block_goes_to_a_new_stream(self):
        rec, streams = self.recognizer(['after '])
        events = self.start(rec)
        self.overflow(events)  # before the worker starts, so nothing is recognised yet
        self.worker.start()
        events.wait('ready')
        events.snapshot(self.worker)
        self.assertEqual(len(streams), 2)  # the one made when the models loaded, and one after the gap
        self.assertEqual([stream.text for stream in streams], ['', 'after '])

    def test_when_the_new_stream_cannot_be_made_the_old_one_is_not_fed_either(self):
        rec, streams = self.recognizer(['lost '], fail_from=2)
        events = self.start(rec)
        self.overflow(events)
        self.worker.start()
        events.wait('ready')
        self.assertIn('injected stream failure', events.wait('error')[2])
        events.snapshot(self.worker)
        self.assertEqual(len(streams), 1)
        self.assertEqual(rec.script, ['lost '])

    def test_a_broken_stream_is_not_replaced_after_dropped_audio(self):
        rec, streams = self.recognizer(['lost ', 'later '])
        events, release = Events(), threading.Event()

        def on_event(kind, value):
            events(kind, value)
            if kind == 'error':  # the worker waits here, so the audio fed meanwhile overflows its queue
                release.wait(3)
        self.start(rec, on_event)
        self.worker.start()
        events.wait('ready')
        rec.fail = True
        self.worker.feed(0, SPEECH)
        events.wait('error')
        rec.fail = False
        self.overflow(events)
        release.set()
        events.snapshot(self.worker)
        self.assertEqual(len(streams), 1)  # only restart() or the app's retry make a new one
        self.assertEqual(rec.script, ['lost ', 'later '])

    def test_a_block_taken_as_the_source_changes_is_not_recognised(self):
        rec = FakeRecognizer(['old ', 'new '])
        events = self.start(rec)
        take = self.worker._next

        def next_then_switch():  # the window switches sources while the worker holds a block of the old one
            item = take()
            if item and item[0] == 'audio' and item[1][0] == 1:
                self.worker.set_source(2)
            return item
        self.worker._next = next_then_switch
        self.worker.start()
        events.wait('ready')
        self.worker.set_source(1)
        self.worker.feed(1, SPEECH)
        events.snapshot(self.worker)
        events.snapshot(self.worker)  # and the new source's stream has been made
        self.assertEqual(rec.script, ['old ', 'new '])


if __name__ == '__main__':
    unittest.main(verbosity=2)
