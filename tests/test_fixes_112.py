#!/usr/bin/env python3
"""Regressions for the seven 1.1.2 reliability fixes, without native models or GTK."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

from nowcaption import engine
import test_reliability as fixtures
import test_fixes_104 as previous
from test_regressions import APP, ROOT, app_with_fakes


class FaultyFilter(fixtures.FakeFilter):
    def __init__(self, operation, reset_number=1):
        super().__init__()
        self.operation, self.reset_number = operation, reset_number

    def reset(self):
        super().reset()
        if self.operation == 'reset' and self.resets == self.reset_number:
            raise RuntimeError('denoiser reset failed')

    def run(self, samples, rate):
        if self.operation == 'run':
            raise RuntimeError('denoiser inference failed')
        return super().run(samples, rate)

    def flush(self):
        if self.operation == 'flush':
            raise RuntimeError('denoiser flush failed')
        return super().flush()


class BufferedFilter(fixtures.FakeFilter):
    """Holds accepted input until flush, just as a native enhancer holds its tail."""
    def __init__(self):
        super().__init__()
        self.pending = []

    def reset(self):
        super().reset()
        self.pending = []

    def run(self, samples, rate):
        self.runs += 1
        self.pending.append(np.asarray(samples).copy())
        return NS(samples=[])

    def flush(self):
        self.flushes += 1
        tail = np.concatenate(self.pending) if self.pending else []
        self.pending = []
        return NS(samples=tail)


class WorkerTests(unittest.TestCase):
    def start_worker(self, script=('hello ', 'again '), music_filter=None, punct=None):
        events = fixtures.Events()
        worker = engine.RecognitionWorker(
            events, load=lambda: (fixtures.FakeRecognizer(script), punct),
            load_filter=lambda: music_filter)
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

    def test_filter_reset_failure_keeps_captioning_and_snapshots_working(self):
        worker, events = self.start_worker(music_filter=FaultyFilter('reset'))
        worker.set_music_filter(True)
        self.assertFalse(events.wait('music-filter'))
        worker.feed(0, fixtures.SPEECH)
        self.assertIn('Hello', events.snapshot(worker))
        self.assertTrue(any('reset failed' in value for kind, value in events.seen if kind == 'warning'))
        self.assert_healthy(worker, events)

    def test_filter_inference_failure_falls_back_on_the_same_audio_block(self):
        worker, events = self.start_worker(music_filter=FaultyFilter('run'))
        worker.set_music_filter(True)
        events.wait('music-filter')
        worker.feed(0, fixtures.SPEECH)
        self.assertIn('Hello', events.snapshot(worker))
        worker.feed(0, fixtures.SPEECH)
        self.assertIn('hello again', events.snapshot(worker).lower())
        self.assertFalse(worker.music_filter_on)
        self.assertIsNone(worker.music_filter)
        self.assert_healthy(worker, events)

    def test_filter_flush_failure_keeps_future_unfiltered_speech_working(self):
        worker, events = self.start_worker(music_filter=FaultyFilter('flush'))
        worker.set_music_filter(True)
        events.wait('music-filter')
        worker.feed(0, fixtures.SPEECH)
        events.snapshot(worker)
        worker.feed(0, fixtures.SILENCE)
        worker.feed(0, fixtures.SPEECH)
        self.assertIn('hello again', events.snapshot(worker).lower())
        self.assertFalse(worker.music_filter_on)
        self.assert_healthy(worker, events)

    def test_filter_reset_failure_during_clear_does_not_break_the_new_stream(self):
        worker, events = self.start_worker(music_filter=FaultyFilter('reset', reset_number=2))
        worker.set_music_filter(True)
        events.wait('music-filter')
        worker.feed(0, fixtures.SPEECH)
        events.snapshot(worker)
        worker.clear(1)
        events.snapshot(worker)
        worker.feed(0, fixtures.SPEECH)
        text = events.snapshot(worker).lower()
        self.assertIn('again', text)
        self.assertNotIn('hello', text)
        self.assert_healthy(worker, events)

    def test_an_unexpected_command_error_is_reported_and_worker_can_recover(self):
        worker, events = self.start_worker()
        handle = worker._handle
        def fail(kind, value):
            if kind == 'music-filter':
                raise RuntimeError('command failed')
            handle(kind, value)
        with patch.object(worker, '_handle', side_effect=fail):
            worker.set_music_filter(True)
            self.assertIn('command failed', events.wait('error'))
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

    def test_source_change_drains_the_filter_tail_exactly_once(self):
        music_filter = BufferedFilter()
        worker, events = self.start_worker(script=('tail ',), music_filter=music_filter)
        worker.set_music_filter(True)
        events.wait('music-filter')
        worker.feed(0, fixtures.SPEECH)
        self.assertEqual(events.snapshot(worker), '')
        worker.set_source(1)
        text = events.snapshot(worker).lower()
        self.assertEqual(text.count('tail'), 1)
        self.assertEqual(music_filter.flushes, 1)
        worker.set_source(2)
        self.assertEqual(events.snapshot(worker).lower().count('tail'), 1)
        self.assertEqual(music_filter.flushes, 1)

    def test_filter_disable_drains_old_audio_before_new_unfiltered_audio(self):
        music_filter = BufferedFilter()
        worker, events = self.start_worker(script=('tail ', 'next '), music_filter=music_filter)
        worker.set_music_filter(True)
        events.wait('music-filter')
        worker.feed(0, fixtures.SPEECH)
        self.assertEqual(events.snapshot(worker), '')
        worker.set_music_filter(False)
        worker.feed(0, fixtures.SPEECH)
        text = events.snapshot(worker).lower()
        self.assertIn('tail next', text)
        self.assertEqual(text.count('tail'), 1)
        self.assertEqual(music_filter.flushes, 1)

    def test_clear_discards_buffered_filter_audio_instead_of_draining_it(self):
        music_filter = BufferedFilter()
        worker, events = self.start_worker(script=('new ',), music_filter=music_filter)
        worker.set_music_filter(True)
        events.wait('music-filter')
        worker.feed(0, fixtures.SPEECH)
        events.snapshot(worker)
        worker.clear(1)
        self.assertEqual(events.snapshot(worker), '')
        self.assertEqual(music_filter.flushes, 0)
        self.assertEqual(music_filter.pending, [])
        worker.feed(0, fixtures.SPEECH)
        events.snapshot(worker)
        worker.set_music_filter(False)
        self.assertIn('New', events.snapshot(worker))

    def test_frozen_punctuation_survives_source_changes_and_recovery(self):
        worker, events = self.start_worker(
            script=[f'line{i} a b c ' for i in range(7)],
            punct=previous.StablePunctuationTests.FlakyPunctuation())
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


class StatusTests(unittest.TestCase):
    def setUp(self):
        holder = previous.NotificationTests()
        holder.setUp()
        self.addCleanup(holder.tearDown)
        self.window, self.timers = holder.window, holder.timers
        self.app = app_with_fakes()
        self.app.ready = True
        self.app.window = self.window
        self.app.worker = NS(broken=True, set_source=lambda gen: None)
        self.window.show_text('Already displayed speech')

    def test_buffered_captions_and_retry_start_do_not_clear_capture_failure(self):
        self.app.capture_ended(0, 'device disappeared')
        message = self.window.status
        self.app.on_worker('caption', (0, 'Already displayed speech plus buffered words'))
        self.assertEqual(self.window.status, message)
        self.app.start_capture()
        self.assertEqual(self.window.status, message)
        self.app.capture_started(self.app.source_gen - 1)
        self.assertIn('capture', self.window.failures)
        self.app.capture_started(self.app.source_gen)
        self.assertNotIn('capture', self.window.failures)
        self.assertEqual(self.window.status, 'Audio is back.')

    def test_a_temporary_notification_restores_an_unresolved_failure(self):
        self.app.capture_ended(0, 'device disappeared')
        message = self.window.status
        self.window.show_status('Transcript copied.', seconds=3)
        self.window.show_text('More buffered speech')
        self.timers.fire(self.window.status_timer)
        self.assertEqual(self.window.status, message)
        self.assertTrue(self.window.status_sticky)

    def test_recognition_success_cannot_clear_an_unrelated_capture_failure(self):
        self.app.capture_ended(0, 'device disappeared')
        self.app.on_worker('error', 'inference failed')
        self.app.on_worker('caption', (0, 'Replayed old words'))
        self.assertIn('inference failed', self.window.status)
        self.app.on_worker('recognition-ok', 0)
        self.assertNotIn('recognition', self.window.failures)
        self.assertIn('device disappeared', self.window.status)
        self.assertIn('capture', self.window.failures)

    def test_retrying_the_model_replaces_its_load_error_until_it_loads(self):
        restarts = []
        self.app.ready = False
        self.app.worker = NS(restart=lambda: restarts.append(True), set_source=lambda gen: None)
        self.app.on_worker('load-error', 'Could not load the speech model: missing file.')
        self.app.on_source(NS(set_state=lambda value: None), NS(get_string=lambda: 'mic'))
        self.assertEqual(restarts, [True])
        self.assertEqual(self.window.status, 'Retrying the speech model…')
        self.window.show_status('Transcript copied.', seconds=3)
        self.timers.fire(self.window.status_timer)
        self.assertEqual(self.window.status, 'Retrying the speech model…')
        self.app.on_worker('ready', None)
        self.assertNotIn('model', self.window.failures)
        self.assertEqual(self.window.status, '')

    def test_stale_recognition_success_does_not_clear_the_current_failure(self):
        self.app.source_gen = 2
        self.app.on_worker('error', 'current inference failed')
        self.app.on_worker('recognition-ok', 1)
        self.assertIn('recognition', self.window.failures)
        self.assertIn('current inference failed', self.window.status)


class SaveTests(unittest.TestCase):
    def test_export_failure_releases_an_accepted_save_and_cancels_pending_quit(self):
        holder = previous.SaveTests()
        holder.setUp()
        self.addCleanup(holder.tearDown)
        app = holder.app
        events = fixtures.Events()
        worker = engine.RecognitionWorker(events, load=lambda: (fixtures.FakeRecognizer(), None))
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        app.worker = worker
        file = holder.delayed_file()
        with patch.object(worker.captioner, 'transcript', side_effect=RuntimeError('export failed')):
            holder.accept_destination(file)
            app.request_quit()
            self.assertEqual(app.pending_saves, 1)
            app.on_worker('snapshot-error', events.wait('snapshot-error'))
        self.assertEqual(app.pending_saves, 0)
        self.assertEqual(app.holds, 0)
        self.assertFalse(app.quit_requested)
        self.assertEqual(app.quits, 0)
        self.assertIsNone(file.contents)
        self.assertIn('export failed', app.window.statuses[-1])


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'tools').mkdir()
        (self.root / 'bin').mkdir()
        shutil.copy(ROOT / 'tools/run-dev.sh', self.root / 'tools')
        shutil.copy(ROOT / 'requirements.txt', self.root)
        for directory, names in ((engine.ASR_DIR, engine.ASR_FILES),
                                 (engine.PUNCT_DIR, engine.PUNCT_FILES),
                                 (engine.MUSIC_FILTER_DIR, (engine.MUSIC_FILTER_FILE,))):
            folder = self.root / 'models' / directory
            folder.mkdir(parents=True)
            for name in names:
                (folder / name).write_bytes(b'existing model')
        fetch = self.root / 'tools/fetch-models.sh'
        fetch.write_text('#!/bin/sh\necho fetched >> fetch.log\nmkdir -p models/dpdfnet2\nprintf model > models/dpdfnet2/dpdfnet2.onnx\n')
        fetch.chmod(0o755)
        (self.root / 'runtime').write_text('python-current')
        self.fake_python = ('#!' + sys.executable + '\n' + """
import json
import shutil
import sys
from pathlib import Path
root = Path(ROOT_LITERAL)
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:1] == ['-c']:
    if 'sys.implementation.cache_tag' in args[1]:
        print((root / 'runtime').read_text())
    elif 'import sherpa_onnx' in args[1] and (root / 'broken').exists():
        raise SystemExit(1)
elif args[:2] == ['-m', 'venv']:
    target = root / '.venv/bin/python'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(__file__, target)
    target.chmod(0o755)
    (root / 'created').write_text('created')
elif args[:3] == ['-m', 'pip', '--version']:
    raise SystemExit(1 if (root / 'pip-missing').exists() else 0)
elif args[:3] == ['-m', 'pip', 'install']:
    if (root / 'install-fails').exists():
        raise SystemExit(9)
    (root / 'broken').unlink(missing_ok=True)
elif args[:2] == ['-m', 'ensurepip']:
    (root / 'pip-missing').unlink(missing_ok=True)
elif args[:2] == ['-m', 'nowcaption']:
    if (root / 'broken').exists():
        raise SystemExit(1)
    (root / 'launched').write_text('launched')
else:
    raise SystemExit('Unexpected fake Python command: ' + repr(args))
""".replace('ROOT_LITERAL', repr(str(self.root))))
        for path in (self.root / 'bin/python3', self.root / '.venv/bin/python'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.fake_python)
            path.chmod(0o755)
        self.write_stamps()

    def write_stamps(self):
        checksum = hashlib.sha256((self.root / 'requirements.txt').read_bytes()).hexdigest()
        (self.root / '.venv/.deps').write_text(checksum + '\n')
        (self.root / '.venv/.python').write_text((self.root / 'runtime').read_text() + '\n')

    def run_launcher(self):
        env = dict(os.environ, PATH=str(self.root / 'bin') + os.pathsep + os.environ['PATH'])
        return subprocess.run(['sh', 'tools/run-dev.sh'], cwd=self.root, env=env,
                              text=True, capture_output=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in (self.root / 'calls.jsonl').read_text().splitlines()]

    def test_upgrading_an_existing_checkout_fetches_the_missing_filter_model(self):
        (self.root / 'models' / engine.MUSIC_FILTER_DIR / engine.MUSIC_FILTER_FILE).unlink()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'fetch.log').exists())
        self.assertTrue((self.root / 'models' / engine.MUSIC_FILTER_DIR / engine.MUSIC_FILTER_FILE).is_file())

    def test_matching_dependency_stamp_does_not_hide_broken_imports(self):
        (self.root / 'broken').touch()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        installs = [args for args in self.calls() if args[:3] == ['-m', 'pip', 'install']]
        self.assertEqual(len(installs), 1)
        self.assertIn('--force-reinstall', installs[0])
        self.assertTrue((self.root / 'launched').exists())
        self.assertFalse((self.root / 'broken').exists())

    def test_system_python_upgrade_recreates_the_environment_and_updates_its_identity(self):
        (self.root / 'runtime').write_text('python-upgraded')
        (self.root / '.venv/old-interpreter-file').touch()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'created').exists())
        self.assertFalse((self.root / '.venv/old-interpreter-file').exists())
        self.assertEqual((self.root / '.venv/.python').read_text().strip(), 'python-upgraded')
        self.assertTrue((self.root / '.venv/.deps').exists())

    def test_healthy_environment_is_checked_without_reinstalling_packages(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(args[:1] == ['-c'] and 'import sherpa_onnx' in args[1] for args in self.calls()))
        self.assertFalse(any(args[:3] == ['-m', 'pip', 'install'] for args in self.calls()))
        self.assertFalse((self.root / 'created').exists())

    def test_failed_repair_keeps_success_stamps_absent_and_retries_next_time(self):
        (self.root / 'broken').touch()
        (self.root / 'install-fails').touch()
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / '.venv/.deps').exists())
        self.assertFalse((self.root / '.venv/.python').exists())
        self.assertFalse((self.root / 'launched').exists())
        (self.root / 'install-fails').unlink()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / '.venv/.deps').exists())
        self.assertTrue((self.root / '.venv/.python').exists())

    def test_missing_pip_is_bootstrapped_with_the_current_environment_python(self):
        (self.root / 'broken').touch()
        (self.root / 'pip-missing').touch()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['-m', 'ensurepip', '--upgrade'], self.calls())
        self.assertTrue((self.root / 'launched').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)

