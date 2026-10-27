#!/usr/bin/env python3
"""Regression checks for the ten issues fixed in 1.0.3.

The real engine/capture/scoring code is exercised with controlled native dependencies.
Application methods are compiled unchanged with AST so these checks also run without GTK.
"""
import ast
import contextlib
import importlib.util
import io
import json
import queue
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np
from nowcaption import audio, engine
from test_reliability import Events, FakeGst, FakePipeline, FakeRecognizer, FakeStream, SPEECH, SILENCE
from transcript_check import check, words
import capture_print

ROOT = Path(__file__).resolve().parents[1]


def load_app_methods():
    tree = ast.parse((ROOT / 'src/nowcaption/app.py').read_text())
    wanted = {'on_source', 'start_capture', 'on_worker', 'clear_transcript', 'on_shutdown',
              'capture_ended', 'capture_started', 'retry_capture', 'width_for', 'watch_monitor', 'use_monitor',
              'on_monitor_geometry', 'disconnect_monitors', 'on_monitors_changed',
              'cancel_recognition_retry', 'retry_recognition', 'request_quit', 'on_window_close', 'threads_finished',
              'save_transcript', 'write_transcript', 'start_save', 'begin_save', 'finish_save',
              'full_transcript', 'earlier_sessions',
              'show_text', 'show_status', 'show_failure', 'clear_failure', 'end_status',
              'render', 'show_footer', 'caption_height', 'box_height'}
    classes = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in ('NowCaption', 'CaptionWindow'):
            node.bases, node.keywords, node.decorator_list = [], [], []
            node.body = [m for m in node.body if isinstance(m, ast.FunctionDef) and m.name in wanted]
            classes.append(node)
    context = {'os': __import__('os'), 'audio': audio, 'engine': engine,
               'session_heading': lambda started: 'Thursday 23 October 2026, 14:05',
               'LOG': __import__('logging').getLogger('nowcaption'),
               'GLib': NS(timeout_add_seconds=lambda *a: 1, source_remove=lambda *a: None),
               'LayerShell': NS(set_monitor=lambda *a: None),
               'ROWS': 2, 'PAD_Y': 12, 'STATUS_MS': 150}
    exec(compile(ast.fix_missing_locations(ast.Module(body=classes, type_ignores=[])),
                 str(ROOT / 'src/nowcaption/app.py'), 'exec'), context)
    return context


APP = load_app_methods()


def app_with_fakes():
    a = APP['NowCaption']()
    settings = {'source': 'desktop'}
    a.settings = NS(get=settings.get, set=settings.__setitem__)
    a.window = NS(text='', statuses=[])
    a.window.show_text = lambda text: setattr(a.window, 'text', text)
    a.window.show_status = lambda text, seconds=None, sticky=False: a.window.statuses.append(text)
    a.window.show_failure = lambda scope, text: a.window.statuses.append(text)
    a.window.clear_failure = lambda scope: None
    a.ready = a.closing = False
    a.source_gen = a.ui_gen = a.capture_failures = a.recognition_failures = 0
    a.retry_id = None
    a.recognition_retry_id = None
    a.recognition_retry_token = 0
    a.pending_saves = 0
    a.save_queues = {}
    a.quit_requested = False
    a.transcript = None
    a.started = 1792760700.0
    a.history_path = engine.history_file(a.started)
    a.capture = NS(starts=[], start=lambda device, gen: a.capture.starts.append((device, gen)), stop=lambda: None)
    a.fullscreen_watch = NS(stop=lambda sync=False: None)  # the KWin script is not these tests' business
    a.panel_float = NS(stop=lambda: None, dock=lambda edge, output='': None)  # nor are Plasma's panels
    return a


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
        a.worker = self.worker
        a.on_worker('load-error', events.wait('load-error'))
        a.on_source(NS(set_state=lambda v: None), NS(get_string=lambda: 'mic'))
        a.on_worker('ready', events.wait('ready'))
        self.worker.feed(a.source_gen, SPEECH)
        a.on_worker('caption', events.wait('caption', lambda v: 'Recovered' in v[1]))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(a.capture.starts, [(audio.MICROPHONE, 1)])
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
        a.worker = NS(clear=lambda gen: calls.append(('clear', gen)),
                      set_source=lambda gen: calls.append(('source', gen)))
        a.ready = True
        a.window.text = 'old words'
        a.clear_transcript()
        self.assertEqual(calls, [('clear', 1), ('source', 1)])
        self.assertEqual(a.capture.starts, [(audio.DESKTOP, 1)])
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
        a.worker = NS(stop=lambda: calls.append('stop'), join=forbidden_join)
        a.on_shutdown(None)
        self.assertTrue(a.closing)
        self.assertEqual(calls, ['stop'])


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
        cap = audio.Capture(lambda *a: None, on_started=started.put,
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
        cap = audio.Capture(lambda *a: None, on_started=started.put, on_ended=lambda *a: ended.put(a), gst=gst)
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
        cap = audio.Capture(lambda *a: None, on_started=started.append, gst=gst)
        try:
            cap.start('desktop', 1)
            self.assertTrue(entered.wait(1))
            cap.stop()
        finally:
            release.set()
            self.assertTrue(cap.wait_stopped(3))
        self.assertEqual(started, [])
        self.assertEqual(gst.pipelines[0].state, 'null')


class Monitor:
    def __init__(self, width):
        self.width, self.handlers = width, {}

    def get_geometry(self):
        return NS(width=self.width, height=600)

    def connect(self, signal, fn):
        handler = len(self.handlers) + 1
        self.handlers[handler] = (signal, fn)
        return handler

    def disconnect(self, handler):
        del self.handlers[handler]

    def notify(self, prop):
        for signal, fn in list(self.handlers.values()):
            if signal == f'notify::{prop}':
                fn(self, None)


class MonitorRegressions(unittest.TestCase):
    def window(self):
        w = APP['CaptionWindow']()
        w.monitor, w.box_width, w.layer = Monitor(3840), 1000, False
        w._monitor_handlers, w.restyles = [], 0
        w.restyle = lambda: setattr(w, 'restyles', w.restyles + 1)
        return w

    def test_resolution_and_scale_notifications_reflow_the_existing_monitor(self):
        w = self.window()
        w.watch_monitor()
        w.monitor.width = 800
        w.monitor.notify('geometry')
        self.assertEqual(w.box_width, 560)
        w.monitor.notify('scale-factor')
        self.assertEqual(w.restyles, 2)

    def test_switching_monitor_disconnects_old_notifications(self):
        w = self.window()
        w.watch_monitor()
        old = w.monitor
        new = Monitor(800)
        w.use_monitor(new)
        self.assertEqual(old.handlers, {})
        self.assertEqual(len(new.handlers), 2)
        old.width = 400
        old.notify('geometry')
        self.assertEqual(w.box_width, 560)
        model = Monitor(0)
        w._monitors, w._monitors_handler = model, model.connect('items-changed', lambda *a: None)
        w.disconnect_monitors()
        self.assertEqual(new.handlers, {})
        self.assertEqual(model.handlers, {})


class TranscriptRegressions(unittest.TestCase):
    def test_scrambled_fixture_vocabulary_is_rejected(self):
        refs = json.loads((ROOT / 'tests/fixtures/refs.json').read_text())
        vocabulary = sorted({w for r in refs for w in words(r['text'])}, reverse=True)
        output = json.dumps({'kind': 'transcript', 'text': ' '.join(vocabulary)})
        ok, score = check(output, refs)
        self.assertFalse(ok)
        self.assertGreater(score, 0.8)

    def test_correct_transcript_ignores_readiness_logging_and_timestamps(self):
        ref = [{'text': 'hello hello mister speaker'}]
        output = 'READY\nnoise in a native log\n' + json.dumps({'kind': 'transcript',
            'text': '[12:34:56] Hello hello Mr. speaker.'})
        self.assertEqual(check(output, ref), (True, 0))

    def test_capture_error_cannot_pass_even_with_a_correct_transcript(self):
        output = '\n'.join(json.dumps(r) for r in (
            {'kind': 'error', 'message': 'device disconnected'}, {'kind': 'transcript', 'text': 'hello'}))
        with self.assertRaisesRegex(ValueError, 'device disconnected'):
            check(output, [{'text': 'hello'}])

    def test_missing_and_inserted_words_count_as_errors(self):
        ref = [{'text': 'hello hello'}]
        for text in ('hello', 'hello hello wrong extra'):
            ok, score = check(json.dumps({'kind': 'transcript', 'text': text}), ref)
            self.assertFalse(ok)
            self.assertGreaterEqual(score, 0.5)


class CaptureReadinessRegressions(unittest.TestCase):
    def test_ready_waits_for_actual_samples_and_optional_warning_is_allowed(self):
        requested, capture_box, result = threading.Event(), [], []
        class Worker:
            def __init__(self, on_event):
                self.on_event = on_event
            def start(self):
                self.on_event('warning', 'punctuation unavailable')
                self.on_event('ready', None)
            def set_source(self, gen):
                pass
            def feed(self, *args):
                pass
            def snapshot(self, fn):
                self.on_event('snapshot', (fn, 'Hello'))
            def stop(self):
                pass
            def join(self, timeout):
                pass
        class Capture:
            def __init__(self, on_audio, on_started, on_ended):
                self.on_started, self.on_ended = on_started, on_ended
                capture_box.append(self)
            def start(self, device, gen):
                requested.set()
            def stop(self):
                pass
            def wait_stopped(self, timeout):
                return True
        out = io.StringIO()
        with patch.object(capture_print.engine, 'RecognitionWorker', Worker), \
             patch.object(capture_print.audio, 'Capture', Capture), \
             patch.object(capture_print.sys, 'argv', ['capture_print.py', 'test.monitor', '0.01']), \
             contextlib.redirect_stdout(out):
            runner = threading.Thread(target=lambda: result.append(capture_print.main()))
            runner.start()
            try:
                self.assertTrue(requested.wait(1))
                self.assertNotIn('READY', out.getvalue())
                capture_box[0].on_started(1)
            finally:
                if runner.is_alive():
                    capture_box[0].on_started(1)
                runner.join(2)
        self.assertEqual(result, [0])
        self.assertIn('READY\n', out.getvalue())
        self.assertIn('punctuation unavailable', out.getvalue())


class BenchmarkRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('e2e_regression', ROOT / 'benchmark/e2e.py')
        cls.e2e = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.e2e)

    def test_next_clip_cannot_supply_a_missed_word_in_the_previous_clip(self):
        plays = [({'audio': 'a', 'text': 'alpha beta'}, 0), ({'audio': 'b', 'text': 'gamma beta'}, 1)]
        captions = [(1.2, ['gamma', 'beta'])]
        times = {'a': {'0': 0.1, '1': 0.2}, 'b': {'0': 0.1, '1': 0.2}}
        _, details = self.e2e.score(plays, captions, times)
        self.assertTrue(details)
        self.assertEqual({d['clip'] for d in details}, {'b'})

    def test_delayed_word_within_a_clips_own_interval_is_scored(self):
        plays = [({'audio': 'a', 'text': 'alpha beta'}, 0), ({'audio': 'b', 'text': 'gamma'}, 4)]
        times = {'a': {'0': 0.1, '1': 0.2}, 'b': {'0': 0.1}}
        _, details = self.e2e.score(plays, [(1.2, ['alpha', 'beta'])], times)
        self.assertEqual(len(details), 2)
        self.assertEqual({d['clip'] for d in details}, {'a'})

    def test_repeated_reference_words_need_separate_hypothesis_occurrences(self):
        plays = [({'audio': 'a', 'text': 'go go'}, 0)]
        _, details = self.e2e.score(plays, [(0.3, ['go']), (0.9, ['go', 'go'])],
                                    {'a': {'0': 0.1, '1': 0.7}})
        self.assertEqual({d['index']: d['lag'] for d in details}, {0: 0.2, 1: 0.2})


if __name__ == '__main__':
    unittest.main(verbosity=2)
