#!/usr/bin/env python3
"""The repository's scripts, run against copies with fake tools: tools/run-dev.sh sets up and
repairs a checkout's virtual environment and models, and tests/test_flatpak.sh fails fast and
checks the complete, ordered transcript (tests/transcript_check.py) that tests/capture_print.py
prints only once audio arrives.

ScriptTests come from the reliability review (findings 8 and 11), TranscriptRegressions and
CaptureReadinessRegressions from 1.0.3, SetupTests from 1.1.2.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import capture_print
import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from transcript_check import check, words

ROOT = Path(__file__).resolve().parents[2]  # the repository


class ScriptTests(unittest.TestCase):
    """Findings 8 and 11, run against copies of the real scripts with fake tools."""
    ROOT = Path(__file__).resolve().parents[2]

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
        for f in (engine.ASR_DIR + '/' + n for n in engine.ASR_FILES):
            (self.dir / 'models' / f).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / 'models' / f).write_text('x')
        for n in engine.PUNCT_FILES:
            (self.dir / 'models' / engine.PUNCT_DIR).mkdir(parents=True, exist_ok=True)
            (self.dir / 'models' / engine.PUNCT_DIR / n).write_text('x')
        (self.dir / '.venv/bin').mkdir(parents=True)
        self.script(self.dir / 'bin/python3', 'echo FAKE_RUNTIME\n')
        self.script(self.dir / '.venv/bin/python', '''
if [ "$1" = -c ]; then echo FAKE_RUNTIME; exit 0; fi
if [ "$1" = -m ] && [ "$2" = pip ]; then
    [ "$3" = --version ] && exit 0
    echo PIP_INSTALL; exit 1
fi
echo PYTHON "$@"; exit 0
''')
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
                                 (engine.PUNCT_DIR, engine.PUNCT_FILES)):
            folder = self.root / 'models' / directory
            folder.mkdir(parents=True)
            for name in names:
                (folder / name).write_bytes(b'existing model')
        fetch = self.root / 'tools/fetch-models.sh'
        fetch.write_text('#!/bin/sh\necho fetched >> fetch.log\n')
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

    def test_a_checkout_without_the_removed_filter_model_starts_without_fetching(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'fetch.log').exists())
        self.assertTrue((self.root / 'launched').exists())

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
             patch.object(capture_print.engine, 'Capture', Capture), \
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
