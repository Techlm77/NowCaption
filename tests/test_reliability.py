#!/usr/bin/env python3
"""Reliability tests for the recognition worker, capture lifecycle and transcript, with fake
models and a fake GStreamer, so they run anywhere in about a second.

They cover source switching, failures in recognition, punctuation and capture, end of
stream, Clear, Copy/Save content, long sessions, model files and the setup scripts.

    PYTHONPATH=src .venv/bin/python tests/test_reliability.py
"""
import os
import queue
import subprocess
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

from nowcaption import audio, engine

import logging
logging.getLogger('nowcaption').setLevel(logging.CRITICAL)  # the failures below are injected on purpose

SPEECH = np.full(1600, 1000, dtype=np.int16).tobytes()  # 100 ms of non-silent audio
SILENCE = np.zeros(1600, dtype=np.int16).tobytes()


class FakeStream:
    def __init__(self, rec):
        self.rec, self.text = rec, ''
        self.ready = False

    def accept_waveform(self, rate, samples):
        rec = self.rec
        rec.threads.add(threading.current_thread().name)
        with rec.lock:
            rec.active += 1
            rec.max_active = max(rec.max_active, rec.active)
        try:
            if rec.fail:
                raise RuntimeError('injected recognizer failure')
            if rec.slow:
                time.sleep(rec.slow)
            if np.any(samples) and rec.script:
                self.text += rec.script.pop(0)
            self.ready = True
        finally:
            with rec.lock:
                rec.active -= 1


class FakeRecognizer:
    """Appends the next scripted word for each block of speech."""

    def __init__(self, script=()):
        self.script, self.text = list(script), ''
        self.fail, self.slow = False, 0
        self.threads, self.lock, self.active, self.max_active = set(), threading.Lock(), 0, 0

    def create_stream(self):
        return FakeStream(self)

    def is_ready(self, stream):
        return stream.ready

    def decode_stream(self, stream):
        stream.ready = False

    def get_result(self, stream):
        return stream.text

    def reset(self, stream):
        stream.text = ''
        stream.ready = False


class FakePunct:
    def __init__(self, fail=False):
        self.fail = fail

    def add_punctuation_with_case(self, text):
        if self.fail:
            raise RuntimeError('injected punctuation failure')
        return text[:1].upper() + text[1:] + '.'


class Events:
    """Collects worker events; wait() blocks until one matching predicate arrives."""

    def __init__(self):
        self.q, self.seen = queue.Queue(), []

    def __call__(self, kind, value):
        self.q.put((kind, value))

    def wait(self, kind, pred=lambda v: True, timeout=3):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                k, v = self.q.get(timeout=end - time.monotonic())
            except queue.Empty:
                break
            self.seen.append((k, v))
            if k == kind and pred(v):
                return v
        raise AssertionError(f'no {kind!r} event; saw {self.seen[-10:]}')

    def snapshot(self, worker):
        box = []
        worker.snapshot(box.append)
        fn, text = self.wait('snapshot')
        fn(text)
        return box[0]


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
        self.assertIn('injected recognizer failure', events.wait('error'))
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
        extra = int(worker.REPORT_DROPPED_S / 0.04)
        for _ in range(worker.MAX_BACKLOG + extra):
            worker.feed(0, block)
        self.assertEqual(worker.backlog, worker.MAX_BACKLOG)
        self.assertEqual(len(reports), 1)
        kind, seconds = reports[0]
        self.assertEqual(kind, 'overload')
        self.assertAlmostEqual(seconds, worker.REPORT_DROPPED_S)


# --- capture lifecycle with a fake GStreamer ------------------------------------------------

class FakeGst:
    State = NS(PLAYING='playing', NULL='null')
    StateChangeReturn = NS(FAILURE='failure', SUCCESS='success')
    MessageType = NS(ERROR=1, EOS=2)
    MSECOND = 1_000_000
    Structure = NS(new_from_string=lambda s: s)

    def __init__(self, behaviour):
        self.behaviour, self.pipelines = behaviour, []

    def init(self, arg):
        pass

    def parse_launch(self, description):
        p = FakePipeline(self.behaviour)
        self.pipelines.append(p)
        return p


class FakePipeline:
    def __init__(self, behaviour):
        self.behaviour, self.state, self.pulls = behaviour, None, 0
        self.message = None

    def get_by_name(self, name):
        return self

    def get_bus(self):
        return self

    def set_property(self, name, value):
        if name == 'device':
            self.device = value

    def get_property(self, name):
        # like pulsesrc: a device that doesn't exist is silently replaced by the default
        return 'default.monitor' if self.behaviour == 'missing' else self.device

    def set_state(self, state):
        self.state = state
        return 'failure' if self.behaviour == 'refuse' and state == 'playing' else 'success'

    def pop_filtered(self, kinds):
        m, self.message = self.message, None
        return m

    def is_eos(self):
        return self.behaviour == 'eos'

    def try_pull_sample(self, timeout):
        self.pulls += 1
        if self.state == 'null':
            return None
        if self.behaviour == 'eos':
            return None
        if self.behaviour == 'error' and self.pulls == 3:
            self.message = NS(type=FakeGst.MessageType.ERROR,
                              parse_error=lambda: (NS(message='device disappeared'), None))
        time.sleep(0.002)
        data = SPEECH
        return NS(get_buffer=lambda: NS(extract_dup=lambda a, b: data, get_size=lambda: len(data)))


class CaptureTests(unittest.TestCase):
    def capture(self, behaviour, on_audio=lambda gen, pcm: None):
        ended, started = queue.Queue(), []
        gst = FakeGst(behaviour)
        cap = audio.Capture(on_audio, on_started=started.append,
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


class ModelFileTests(unittest.TestCase):
    """Finding 8: incomplete model folders are never used."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = os.environ.get('NOWCAPTION_MODELS')

    def tearDown(self):
        if self.old is None:
            os.environ.pop('NOWCAPTION_MODELS', None)
        else:
            os.environ['NOWCAPTION_MODELS'] = self.old
        self.tmp.cleanup()

    def make(self, sizes):
        d = self.root / engine.ASR_DIR
        d.mkdir(parents=True, exist_ok=True)
        for name, size in zip(engine.ASR_FILES, sizes, strict=True):
            (d / name).write_bytes(b'x' * size)

    def test_incomplete_explicit_model_folder_is_rejected(self):
        self.make([10, 10, 0, 10])  # a cut-off download
        os.environ['NOWCAPTION_MODELS'] = str(self.root)
        with self.assertRaises(FileNotFoundError):
            engine.models_dir()

    def test_complete_explicit_model_folder_is_used(self):
        self.make([10, 10, 10, 10])
        os.environ['NOWCAPTION_MODELS'] = str(self.root)
        self.assertEqual(engine.models_dir(), self.root)

    def test_missing_punctuation_model_still_loads_with_a_warning(self):
        warnings = []
        real = (engine.load_recognizer, engine.load_punctuation)
        def no_punct():
            raise FileNotFoundError('punctuation model is missing')
        try:
            engine.load_recognizer, engine.load_punctuation = (lambda: FakeRecognizer()), no_punct
            rec, punct = engine.load_models(warnings.append)
        finally:
            engine.load_recognizer, engine.load_punctuation = real
        self.assertIsInstance(rec, FakeRecognizer)
        self.assertIsNone(punct)
        self.assertIn('Punctuation is off', warnings[0])


class TimestampTests(unittest.TestCase):
    def test_unfinished_words_are_timestamped_when_they_started(self):
        rec = FakeRecognizer(['hello ', 'there '])
        cap = engine.Captioner(rec)
        t0 = time.time()
        cap.feed(SPEECH)
        cap.partial_since -= 120  # pretend the sentence began two minutes ago
        cap.feed(SPEECH)
        stamp = cap.transcript().split(']')[0].strip('[')
        self.assertEqual(stamp, time.strftime('%H:%M:%S', time.localtime(t0 - 120)))


class ScriptTests(unittest.TestCase):
    """Findings 8 and 11, run against copies of the real scripts with fake tools."""
    ROOT = Path(__file__).resolve().parents[1]

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for sub in ('tools', 'tests', 'bin'):
            (self.dir / sub).mkdir()
        shutil.copy(self.ROOT / 'tools/run-dev.sh', self.dir / 'tools')
        shutil.copy(self.ROOT / 'tests/test_flatpak.sh', self.dir / 'tests')
        shutil.copy(self.ROOT / 'tests/transcript_check.py', self.dir / 'tests')
        shutil.copy(self.ROOT / 'requirements.txt', self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def script(self, path, body):
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)

    def run_sh(self, *args, timeout=20):
        env = dict(os.environ, PATH=f"{self.dir / 'bin'}{os.pathsep}{os.environ['PATH']}")
        return subprocess.run(['sh', *args], cwd=self.dir, env=env, capture_output=True, text=True,
                              timeout=timeout)

    def test_empty_models_folder_is_refetched_and_failed_install_is_retried(self):
        (self.dir / 'models').mkdir()  # left behind by an interrupted first run
        self.script(self.dir / 'tools/fetch-models.sh', 'echo FETCHED; exit 3\n')
        r = self.run_sh('tools/run-dev.sh')
        self.assertIn('FETCHED', r.stdout)
        self.assertNotEqual(r.returncode, 0)
        # models present now, but a previous pip install failed: no marker, so it must install again
        for f in ('nowcaption-fastconformer-en-80ms-cc-by-v1/' + n for n in engine.ASR_FILES):
            (self.dir / 'models' / f).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / 'models' / f).write_text('x')
        for n in engine.PUNCT_FILES:
            (self.dir / 'models' / engine.PUNCT_DIR).mkdir(parents=True, exist_ok=True)
            (self.dir / 'models' / engine.PUNCT_DIR / n).write_text('x')
        (self.dir / '.venv/bin').mkdir(parents=True)
        self.script(self.dir / '.venv/bin/python', 'echo PYTHON "$@"; exit 0\n')
        self.script(self.dir / '.venv/bin/pip', 'echo PIP_INSTALL; exit 1\n')
        r = self.run_sh('tools/run-dev.sh')
        self.assertIn('PIP_INSTALL', r.stdout)
        self.assertFalse((self.dir / '.venv/.deps').exists())
        self.assertNotEqual(r.returncode, 0)

    def test_flatpak_test_fails_fast_and_cleans_up_when_the_app_cannot_start(self):
        self.script(self.dir / 'bin/flatpak', 'echo cannot start; exit 1\n')
        self.script(self.dir / 'bin/pactl', 'echo "$@" >> "$(dirname "$0")/pactl.log"; echo 99\n')
        r = self.run_sh('tests/test_flatpak.sh', timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('exited before it was ready', r.stdout)
        self.assertIn('unload-module 99', (self.dir / 'bin/pactl.log').read_text())

    def test_flatpak_script_uses_the_ordered_transcript_checker(self):
        import json
        refs = [{'text': 'hello hello mister speaker'}]
        fixtures = self.dir / 'tests/fixtures'
        fixtures.mkdir()
        (fixtures / 'refs.json').write_text(json.dumps(refs))
        (fixtures / 'clip.wav').write_bytes(b'fixture')
        self.script(self.dir / 'bin/pactl', 'echo "$@" >> "$(dirname "$0")/pactl.log"; echo 99\n')
        self.script(self.dir / 'bin/paplay', 'exit 0\n')
        self.script(self.dir / 'bin/flatpak', 'cat "$(dirname "$0")/../record.txt"; sleep 1\n')
        for text, expected in (('Hello hello Mr. speaker.', 0), ('speaker mister hello', 1)):
            with self.subTest(text=text):
                (self.dir / 'record.txt').write_text('READY\n' + json.dumps(
                    {'kind': 'transcript', 'text': '[12:34:56] ' + text}) + '\n')
                r = self.run_sh('tests/test_flatpak.sh', timeout=10)
                self.assertEqual(r.returncode, expected, r.stdout + r.stderr)
                self.assertIn('Word error rate:', r.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
