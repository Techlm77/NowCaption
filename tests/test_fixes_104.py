#!/usr/bin/env python3
"""Recovery, notification, save-on-Quit and full-history benchmark regressions.

Runs real worker threads and unchanged application methods, with controlled
native models, timer dispatch and async file completion. GTK smoke tests live
in test_window.py; these tests do not require PyGObject.
"""
import contextlib
import importlib.util
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np
from nowcaption import engine
from test_regressions import APP, ROOT, app_with_fakes
from test_reliability import Events, FakeRecognizer, FakeStream, SPEECH, SILENCE


class Timers:
    def __init__(self):
        self.pending, self.delays, self.serial = {}, [], 0

    def timeout_add_seconds(self, seconds, callback, *args):
        self.serial += 1
        self.pending[self.serial] = lambda: callback(*args)
        self.delays.append(seconds)
        return self.serial

    def source_remove(self, timer):
        return self.pending.pop(timer, None) is not None

    def fire(self, timer):
        return self.pending.pop(timer)()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.timers = Timers()
        self.patch = patch.dict(APP, GLib=self.timers)
        self.patch.start()
        self.app = app_with_fakes()
        self.app.ready = True
        self.events = Events()

    def tearDown(self):
        if getattr(self, 'worker', None):
            self.worker.stop()
            self.worker.join(3)
            self.assertFalse(self.worker.is_alive())
        self.patch.stop()

    def start_worker(self, recognizer):
        self.worker = engine.RecognitionWorker(self.events, load=lambda: (recognizer, None))
        self.app.worker = self.worker
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
            self.app.on_worker(kind, value)

    def test_consecutive_decode_failures_back_off_then_stop_after_three_retries(self):
        rec = FakeRecognizer()
        rec.fail = True
        self.start_worker(rec)
        for failure in range(1, 5):
            self.worker.feed(0, SPEECH)
            self.barrier()
            self.assertEqual(self.app.recognition_failures, failure)
            if failure <= 3:
                self.timers.fire(self.app.recognition_retry_id)
                self.barrier()  # replaying empty captions must not count as recognition success
                self.assertEqual(self.app.recognition_failures, failure)
        self.assertEqual(self.timers.delays, [2, 4, 8])
        self.assertEqual(self.timers.pending, {})
        self.assertIn('choose a sound source', self.app.window.statuses[-1])

    def test_replayed_old_words_do_not_reset_the_failure_count(self):
        rec = FakeRecognizer(['already recognised '])
        self.start_worker(rec)
        self.worker.feed(0, SPEECH)
        self.barrier()
        rec.fail = True
        self.worker.feed(0, SPEECH)
        self.barrier()
        self.timers.fire(self.app.recognition_retry_id)
        self.barrier()
        self.assertEqual(self.app.recognition_failures, 1)
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
                self.assertEqual(self.app.recognition_failures, failure - 1)
            self.worker.feed(0, SPEECH)
            self.barrier()
            self.assertEqual(self.app.recognition_failures, failure)
            if failure <= 3:
                self.timers.fire(self.app.recognition_retry_id)
                self.barrier()
        self.assertEqual(self.timers.delays, [2, 4, 8])
        self.assertEqual(self.timers.pending, {})

    def test_actual_success_resets_backoff_even_without_new_words(self):
        rec = FakeRecognizer()
        rec.fail = True
        self.start_worker(rec)
        self.worker.feed(0, SPEECH)
        self.barrier()
        self.assertEqual(self.app.recognition_failures, 1)
        rec.fail = False
        self.timers.fire(self.app.recognition_retry_id)
        self.barrier()
        self.assertEqual(self.app.recognition_failures, 1)
        self.worker.feed(0, SILENCE)
        self.barrier()
        self.assertEqual(self.app.recognition_failures, 0)
        self.assertEqual(self.timers.pending, {})

    def test_manual_source_change_invalidates_an_already_dispatched_timer(self):
        rec = FakeRecognizer()
        rec.fail = True
        self.start_worker(rec)
        self.worker.feed(0, SPEECH)
        self.barrier()
        old_timer = self.app.recognition_retry_id
        old_callback = self.timers.pending[old_timer]
        rec.fail = False
        self.app.on_source(NS(set_state=lambda value: None), NS(get_string=lambda: 'mic'))
        self.barrier()
        recovered_stream = self.worker.captioner.stream
        self.assertNotIn(old_timer, self.timers.pending)
        old_callback()  # cancellation cannot recall a callback the UI already dispatched
        self.barrier()
        self.assertIs(self.worker.captioner.stream, recovered_stream)

    def test_clear_and_shutdown_cancel_pending_recognition_timers(self):
        self.app.worker = NS(clear=lambda gen: None, set_source=lambda gen: None,
                             stop=lambda: None, restart=lambda **kwargs: None)
        self.app.on_worker('error', 'failure')
        old = self.app.recognition_retry_id
        self.app.clear_transcript()
        self.assertNotIn(old, self.timers.pending)
        self.app.on_worker('error', 'another failure')
        old = self.app.recognition_retry_id
        self.app.on_shutdown(None)
        self.assertNotIn(old, self.timers.pending)

    def test_stale_success_from_an_old_source_cannot_reset_backoff(self):
        self.app.source_gen, self.app.recognition_failures = 3, 2
        self.app.on_worker('recognition-ok', 2)
        self.assertEqual(self.app.recognition_failures, 2)

    def test_queued_error_is_ignored_after_source_recovery_has_already_succeeded(self):
        self.app.worker = NS(broken=False)
        self.app.on_worker('error', 'an error from before the source change')
        self.assertEqual(self.app.recognition_failures, 0)
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


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.timers = Timers()
        self.patch = patch.dict(APP, GLib=self.timers)
        self.patch.start()
        self.window = APP['CaptionWindow']()
        window = self.window
        window.text, window.status, window.status_timer = '', '', None
        window.status_sticky = False
        window.failures, window.status_scope = {}, None
        window.x = window.y = 0
        window.row_height, window.status_height, window.rows = 30, 24, 2
        window.layout = None
        window.label = NS(set_text=lambda text: setattr(window, 'visible', text))
        window.status_label = NS(
            set_text=lambda text: setattr(window, 'notice', text),
            set_tooltip_text=lambda text: setattr(window, 'tooltip', text),
            set_visible=lambda visible: setattr(window, 'notice_visible', visible))
        window.place = lambda x, y: setattr(window, 'placed_height', window.box_height())

    def tearDown(self):
        self.patch.stop()

    def test_copy_save_warnings_and_overload_never_replace_caption_text(self):
        for message, seconds in [('Transcript copied.', 3), ('Transcript saved.', 4),
                                 ('Punctuation disabled.', 6), ('Could not save.', 8),
                                 ('Falling behind.', 4)]:
            self.window.show_status(message, seconds)
            self.window.show_text('current live speech')
            self.assertEqual(self.window.visible, 'current live speech')
            self.assertEqual(self.window.notice, message)
            self.assertTrue(self.window.notice_visible)
            self.assertEqual(self.window.tooltip, message)

    def test_first_caption_immediately_replaces_timed_status_in_main_caption_rows(self):
        self.window.show_status('Audio is back.', 3)
        self.assertEqual(self.window.visible, 'Audio is back.')
        self.window.show_text('first words after recovery')
        self.assertEqual(self.window.visible, 'first words after recovery')
        self.assertTrue(self.window.notice_visible)

    def test_footer_disappears_and_input_height_shrinks_when_notification_expires(self):
        self.window.show_text('live speech')
        height = self.window.box_height()
        self.window.show_status('Transcript copied.', 3)
        self.assertEqual(self.window.placed_height, height + self.window.status_height)
        self.timers.fire(self.window.status_timer)
        self.assertFalse(self.window.notice_visible)
        self.assertEqual(self.window.placed_height, height)
        self.assertEqual(self.window.visible, 'live speech')

    def test_save_completion_status_stays_visible_while_live_captions_continue(self):
        self.window.show_status('Finishing transcript save before quitting…', sticky=True)
        self.window.show_text('speech while the destination is slow')
        self.assertEqual(self.window.visible, 'speech while the destination is slow')
        self.assertTrue(self.window.notice_visible)
        self.assertEqual(self.window.notice, 'Finishing transcript save before quitting…')

    def test_listening_status_is_cleared_by_the_first_caption(self):
        self.window.show_status('Listening…')
        self.window.show_text('first words')
        self.assertEqual(self.window.visible, 'first words')
        self.assertFalse(self.window.notice_visible)


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.app = app_with_fakes()
        self.app.ready = True
        self.app.holds = self.app.quits = 0
        self.app.hold = lambda: setattr(self.app, 'holds', self.app.holds + 1)
        self.app.release = lambda: setattr(self.app, 'holds', self.app.holds - 1)
        self.app.quit = lambda: setattr(self.app, 'quits', self.app.quits + 1)
        self.glib = NS(
            Error=type('FileError', (Exception,), {'message': 'disk full'}),
            Bytes=NS(new=lambda content: content),
            source_remove=lambda timer: None,
            DateTime=NS(new_now_local=lambda: NS(format=lambda pattern: 'captions.txt')))
        self.patch = patch.dict(APP, GLib=self.glib, Gio=NS(FileCreateFlags=NS(REPLACE_DESTINATION=1)))
        self.patch.start()

    def tearDown(self):
        self.assertEqual(self.app.holds, 0, 'an accepted save leaked an application hold')
        self.patch.stop()

    def delayed_file(self, fail=False, immediate_error=False):
        file = NS(contents=None, callback=None)
        def write(contents, etag, backup, flags, cancellable, callback):
            if immediate_error:
                raise OSError('destination cannot start writing')
            file.contents, file.callback = contents, callback
        def finish(result):
            if fail:
                raise self.glib.Error('disk full')
        file.replace_contents_bytes_async, file.replace_contents_finish = write, finish
        file.get_basename = lambda: 'captions.txt'
        return file

    def accept_destination(self, file):
        dialog = NS(save_finish=lambda result: file)
        dialog.save = lambda parent, cancellable, chosen: chosen(dialog, None)
        with patch.dict(APP, Gtk=NS(FileDialog=lambda **kwargs: dialog)):
            self.app.save_transcript()

    def test_quit_waits_for_queued_snapshot_and_delayed_write(self):
        file = self.delayed_file()
        callbacks = []
        self.app.worker = NS(snapshot=lambda callback, on_error=None: callbacks.append(callback))
        self.accept_destination(file)
        self.assertEqual(self.app.pending_saves, 1)
        self.assertEqual(self.app.holds, 1)
        self.app.request_quit()
        self.assertEqual(self.app.quits, 0)
        self.app.on_worker('snapshot', (callbacks.pop(), 'requested transcript'))
        self.assertEqual(file.contents, b'requested transcript\n')
        self.assertEqual(self.app.quits, 0)
        file.callback(file, None)
        self.assertEqual(self.app.quits, 1)
        self.assertEqual(self.app.pending_saves, 0)

    def test_close_button_uses_the_same_save_completion_rule(self):
        file = self.delayed_file()
        self.app.write_transcript(file, 'close requested')
        self.assertTrue(self.app.on_window_close(None))
        self.assertEqual(self.app.quits, 0)
        file.callback(file, None)
        self.assertEqual(self.app.quits, 1)

    def test_multiple_accepted_saves_finish_before_quitting(self):
        first, second = self.delayed_file(), self.delayed_file()
        self.app.write_transcript(first, 'first')
        self.app.write_transcript(second, 'second')
        self.app.request_quit()
        first.callback(first, None)
        self.assertEqual(self.app.quits, 0)
        self.assertEqual(self.app.pending_saves, 1)
        second.callback(second, None)
        self.assertEqual(self.app.quits, 1)

    def test_failed_save_cancels_deferred_quit_and_keeps_error_visible(self):
        file = self.delayed_file(fail=True)
        self.app.write_transcript(file, 'must not disappear')
        self.app.request_quit()
        file.callback(file, None)
        self.assertEqual(self.app.quits, 0)
        self.assertFalse(self.app.quit_requested)
        self.assertEqual(self.app.pending_saves, 0)
        self.assertIn('disk full', self.app.window.statuses[-1])
        self.app.request_quit()
        self.assertEqual(self.app.quits, 1)

    def test_capture_retries_continue_while_quit_waits_for_a_save(self):
        timers = Timers()
        file = self.delayed_file(fail=True)
        self.app.worker = NS(set_source=lambda gen: None)
        self.app.write_transcript(file, 'words')
        self.app.request_quit()
        with patch.dict(APP, GLib=timers):
            self.app.capture_ended(self.app.source_gen, 'device went away')
            self.assertIsNotNone(self.app.retry_id)
            timers.fire(self.app.retry_id)
        self.assertEqual(len(self.app.capture.starts), 1)
        file.callback(file, None)
        self.assertFalse(self.app.quit_requested)

    def test_write_setup_failure_releases_its_hold(self):
        self.app.write_transcript(self.delayed_file(immediate_error=True), 'words')
        self.assertEqual(self.app.pending_saves, 0)
        self.assertIn('cannot start writing', self.app.window.statuses[-1])

    def test_snapshot_request_failure_releases_its_hold(self):
        def fail(callback, on_error=None):
            raise RuntimeError('snapshot failed')
        self.app.worker = NS(snapshot=fail)
        self.accept_destination(self.delayed_file())
        self.assertEqual(self.app.pending_saves, 0)
        self.assertIn('snapshot failed', self.app.window.statuses[-1])

    def test_quit_without_pending_saves_is_immediate(self):
        self.app.request_quit()
        self.assertEqual(self.app.quits, 1)


class TraceBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('e2e_104', ROOT / 'benchmark/e2e.py')
        cls.e2e = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.e2e)

    def test_full_worker_trace_keeps_every_word_after_actual_caption_window_scrolls(self):
        reference = [f'word{i:03}' for i in range(120)]
        patcher = patch.object(engine.Captioner, 'DISPLAY_LINES', 4)  # make the window scroll
        patcher.start()
        self.addCleanup(patcher.stop)  # enterContext() needs Python 3.11+
        events, traces = Events(), []
        worker = engine.RecognitionWorker(events, load=lambda: (FakeRecognizer(
            [word + ' ' for word in reference]), None), on_trace=lambda text: traces.append(self.e2e.words(text)))
        worker.start()
        try:
            events.wait('ready')
            for _ in reference:
                worker.feed(0, SPEECH)
                events.snapshot(worker)  # avoid an artificial producer overload
            for _ in range(12):
                worker.feed(0, SILENCE)
                events.snapshot(worker)
            normal = [self.e2e.words(value[1]) for kind, value in events.seen if kind == 'caption']
            self.assertEqual(len(normal[-1]), 80)
            self.assertEqual(traces[-1], reference)
            plays = [({'audio': 'long.wav', 'text': ' '.join(reference)}, 0.0)]
            captions = [((i + 1) * 0.1, words) for i, words in enumerate(traces)]
            times = {'long.wav': {str(i): (i + 1) * 0.1 - 0.01 for i in range(120)}}
            lags, details = self.e2e.score(plays, captions, times)
            self.assertEqual(len(lags), 120)
            self.assertEqual({detail['index'] for detail in details}, set(range(120)))
            self.assertAlmostEqual(details[0]['lag'], 0.01)
        finally:
            worker.stop()
            worker.join(3)
            self.assertFalse(worker.is_alive())

    def test_repeated_words_use_distinct_positions_across_clip_boundaries(self):
        plays = [({'audio': 'first', 'text': 'go go'}, 0),
                 ({'audio': 'second', 'text': 'go go'}, 2)]
        captions = [(0.3, ['go']), (0.9, ['go', 'go']),
                    (2.3, ['go', 'go', 'go']), (2.9, ['go', 'go', 'go', 'go'])]
        times = {clip: {'0': 0.1, '1': 0.7} for clip in ('first', 'second')}
        _, detail = self.e2e.score(plays, captions, times)
        self.assertEqual(len(detail), 4)
        self.assertEqual({(d['clip'], d['index']): d['lag'] for d in detail},
                         {(clip, index): 0.2 for clip in times for index in (0, 1)})

    def test_an_excerpt_that_loses_the_session_prefix_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'complete worker traces'):
            self.e2e.new_words(['prior', 'speech'], ['speech', 'next'])

    def test_benchmark_main_consumes_complete_trace_and_writes_version_three_results(self):
        reference = [f'word{i:03}' for i in range(120)]
        workers, cleanup = [], []
        class Worker:
            def __init__(self, on_event, on_trace=None):
                self.on_event, self.on_trace = on_event, on_trace
                workers.append(self)
            def start(self):
                self.on_event('ready', None)
            def set_source(self, gen):
                pass
            def feed(self, *args):
                pass
            def stop(self):
                cleanup.append('worker')
            def join(self, seconds):
                pass
        class Capture:
            def __init__(self, on_audio, on_started, on_ended):
                self.on_started = on_started
            def start(self, device, gen):
                self.on_started(gen)
            def stop(self):
                cleanup.append('capture')
            def wait_stopped(self, seconds):
                return True
        def subprocess_run(command, **kwargs):
            if command[0] == 'paplay':
                workers[0].on_event('caption', (0, ' '.join(reference[-80:])))
                workers[0].on_trace(' '.join(reference))
            else:
                cleanup.append('sink')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'clips'
            data.mkdir()
            (data / 'refs.json').write_text(json.dumps([{'audio': 'long.wav', 'text': ' '.join(reference)}]))
            (data / 'word_times.json').write_text(json.dumps({'long.wav': {
                str(i): (i + 1) / 10 for i in range(120)}}))
            output = io.StringIO()
            with patch.object(self.e2e, 'ROOT', root), \
                 patch.object(self.e2e.engine, 'RecognitionWorker', Worker), \
                 patch.object(self.e2e.audio, 'Capture', Capture), \
                 patch.object(self.e2e.subprocess, 'check_output', return_value=b'42'), \
                 patch.object(self.e2e.subprocess, 'run', side_effect=subprocess_run), \
                 patch.object(self.e2e.time, 'sleep', return_value=None), \
                 patch.object(self.e2e.sys, 'argv', ['e2e.py', '--data', str(data), '--stride', '1']), \
                 contextlib.redirect_stdout(output):
                self.e2e.main()
            result = json.loads(output.getvalue())
            self.assertEqual(result['score_version'], 3)
            self.assertEqual(result['timed_words'], 120)
            self.assertEqual(result['missed_timed_words'], 0)
            self.assertEqual(result['timed_word_coverage'], 1)
            self.assertTrue((root / 'results/e2e-v3-clips.json').is_file())
        self.assertEqual(cleanup, ['capture', 'worker', 'sink'])



class StablePunctuationTests(unittest.TestCase):
    class FlakyPunctuation:
        """Moves its commas whenever the passage grows, like the real model reconsidering."""
        def add_punctuation_with_case(self, text):
            words = text.split()
            return ' '.join(w + (',' if (i + len(words)) % 3 == 0 else '') for i, w in enumerate(words))

    def test_older_lines_keep_their_punctuation_as_new_words_arrive(self):
        captioner = engine.Captioner(NS(create_stream=lambda: None), self.FlakyPunctuation())
        before = ''
        for n in range(10):
            captioner.lines.append((0, '', f'line{n} a b c'))
            for partial in ('', 'x', 'x y', 'x y z'):
                captioner.partial = partial
                now = captioner.caption_text()
                settled = max(0, len(captioner.lines) - captioner.LIVE_LINES)
                frozen = ' '.join(now.split()[:4 * settled])
                self.assertTrue(before.startswith(frozen), (before, now))
                before = now
        self.assertEqual(len(before.split()), 4 * 10 + 3)

    def test_passage_still_holds_only_the_last_display_lines(self):
        captioner = engine.Captioner(NS(create_stream=lambda: None), self.FlakyPunctuation())
        captioner.lines = [(0, f'line{n}', f'line{n}') for n in range(10)]
        with patch.object(engine.Captioner, 'DISPLAY_LINES', 3):
            self.assertEqual(captioner.caption_text().replace(',', '').split(), ['line7', 'line8', 'line9'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
