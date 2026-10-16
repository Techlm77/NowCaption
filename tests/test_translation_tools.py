#!/usr/bin/env python3
"""Benchmark completion and training-data failures, without models, downloads or a GPU."""
import contextlib
import importlib.util
import io
import json
import os
import queue
import runpy
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

from nowcaption import engine
from test_reliability import FakeRecognizer
from test_translation import EchoTranslator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools' / 'translation-model'
sys.path.insert(0, str(TOOLS))
import data_manifest


def load(path):
    spec = importlib.util.spec_from_file_location('test_tool_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


live = load(ROOT / 'benchmark' / 'translation' / 'live_languages.py')


class BenchmarkTests(unittest.TestCase):
    def test_duplicate_caption_events_do_not_move_completion_time(self):
        score = live.score_clip([(1, 'The answer.'), (3, 'The answer.')],
                                on=0.5, off=1.5, end=4, complete=True)
        self.assertEqual((score['first_s'], score['final_s']), (0.5, -0.5))

    def test_new_passages_count_but_empty_and_duplicate_events_do_not(self):
        score = live.score_clip([(0, 'old'), (1, 'old'), (2, ''), (3, 'new'),
                                 (4, 'new words'), (5, 'new words'), (9, 'next clip')],
                                on=0.5, off=4.5, end=6, complete=True)
        self.assertEqual((score['first_s'], score['final_s']), (2.5, -0.5))

    def test_no_output_clip_remains_in_totals_and_arrays(self):
        scores = [live.score_clip([(1, 'English')], 0, 2, 4, True),
                  live.score_clip([], 5, 7, 9, True)]
        records = [{'lang': 'es', 'clip': str(i), **s} for i, s in enumerate(scores)]
        result, = live.latency_summary(records)
        self.assertEqual((result['clips'], result['completed'], result['failed']), (2, 1, 1))
        self.assertEqual(result['first_all'], [1, None])
        self.assertEqual(result['final_all'], [-1, None])
        self.assertEqual(result['results'][1]['status'], 'no_caption')

    def test_a_quiet_passage_without_verified_completion_has_no_final_latency(self):
        score = live.score_clip([(1, 'unfinished')], 0, 2, 6, False, 'timeout')
        self.assertEqual(score['first_s'], 1)
        self.assertIsNone(score['final_s'])
        self.assertEqual((score['status'], score['reason']), ('unfinished', 'timeout'))

    def test_event_wait_reports_failure_instead_of_waiting_for_ready(self):
        events = queue.Queue()
        events.put((0, 'translation-error', 'model failed'))
        with self.assertRaisesRegex(RuntimeError, 'model failed'):
            live.wait_event(events, 'ready', [], timeout=0.05)

    def test_event_wait_has_a_deadline(self):
        with self.assertRaisesRegex(TimeoutError, 'ready'):
            live.wait_event(queue.Queue(), 'ready', [], timeout=0.01)

    def test_accuracy_worker_drains_sentences_then_exits(self):
        result = live.translate_clip(FakeRecognizer(['hola mundo. ']), EchoTranslator(),
                                     np.full(1600, 1000, np.int16), timeout=1)
        self.assertEqual(result, 'HOLA MUNDO.')

    def test_accuracy_inference_error_does_not_hang_with_a_nonempty_queue(self):
        before = {t.ident for t in threading.enumerate()}
        with self.assertRaisesRegex(RuntimeError, 'Translation failed: translation model failed'):
            live.translate_clip(FakeRecognizer(['hola. ']), EchoTranslator(fail=True),
                                np.full(1600, 1000, np.int16), timeout=0.5)
        self.assertFalse([t for t in threading.enumerate()
                          if t.ident not in before and t.name == 'nowcaption-translation'])

    def test_accuracy_timeout_cleans_up_the_worker(self):
        gate = threading.Event()
        timer = threading.Timer(0.15, gate.set)
        timer.start()
        try:
            with self.assertRaisesRegex(TimeoutError, 'did not finish'):
                live.translate_clip(FakeRecognizer(['hola. ']), EchoTranslator(gate),
                                    np.full(1600, 1000, np.int16), timeout=0.01)
        finally:
            gate.set()
            timer.join(1)

    def test_accuracy_reports_the_failing_clip(self):
        with patch.dict(sys.modules, {'sacrebleu': NS()}), \
             patch.object(engine, 'load_translation', return_value=(None, None)), \
             patch.object(live, 'clips', return_value=[{'audio': 'bad.wav', 'english': 'hi'}]), \
             patch.object(live, 'read_wav', return_value=np.zeros(320, np.int16)), \
             patch.object(live, 'translate_clip', side_effect=RuntimeError('inference failed')):
            with self.assertRaisesRegex(RuntimeError, 'es_419/bad.wav: inference failed'):
                live.accuracy('unused', ['es_419'])

    def test_latency_pipeline_reports_a_clip_without_output_and_cleans_up(self):
        clock = NS(now=0.0)
        fake_time = NS(monotonic=lambda: clock.now,
                       sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds))
        workers = []

        class Worker:
            def __init__(self, callback):
                self.callback, self.source_gen = callback, 0
                self.captioner = NS(flushed=True)
                self.translation_worker = NS(lock=threading.RLock(), waiting=[], sentence='',
                                             words='', is_alive=lambda: True)
                self.generations, self.stopped, self.joined = [], False, False
                workers.append(self)

            def start(self):
                self.callback('ready', None)

            def set_translation(self, on):
                self.callback('translation', on)

            def clear(self, gen):
                self.generations.append(gen)

            def snapshot(self, fn):
                self.callback('snapshot', (fn, ''))

            def feed(self, gen, pcm):
                if self.generations[-1] == 1:
                    self.callback('caption', (1, 'English.'))  # repeated events throughout playback

            def stop(self):
                self.stopped = True

            def join_all(self, timeout):
                self.joined = True

        output = io.StringIO()
        with patch.object(engine, 'RecognitionWorker', Worker), patch.object(live, 'time', fake_time), \
             patch.object(live, 'clips', return_value=[{'audio': 'one.wav'}, {'audio': 'two.wav'}]), \
             patch.object(live, 'read_wav', return_value=np.full(1600, 1000, np.int16)), \
             contextlib.redirect_stdout(output):
            live.latency('unused', 2, ['es_419'])
        result = json.loads(output.getvalue())
        self.assertEqual((result['clips'], result['completed'], result['failed']), (2, 1, 1))
        self.assertEqual(result['first_all'], [0, None])
        self.assertEqual(result['final_all'], [-0.1, None])
        self.assertEqual(workers[0].generations, [1, 2])
        self.assertTrue(workers[0].stopped and workers[0].joined)


class SerialPool:
    def __init__(self, *args):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def map(self, fn, items, **kwargs):
        return [fn(item) for item in items]


class TrainingDataTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.data = self.work / 'data'
        (self.data / 'clean').mkdir(parents=True)
        (self.data / 'catalog.json').write_text(json.dumps({
            'es': [{'corpus': 'OpenSubtitles', 'url': 'unused', 'alignment_pairs': 1}]}))
        (self.data / 'fleurs_en_norm.txt').write_text('banned reference')
        self.old_cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, self.old_cwd)
        self.patches = patch.dict(sys.modules, {
            'fasttext': NS(load_model=lambda path: None),
            'opus_stream': NS(pairs=lambda *args: [('hola mundo', 'hello world')])})
        self.patches.start()
        self.addCleanup(self.patches.stop)
        pool = patch('multiprocessing.Pool', SerialPool)
        pool.start()
        self.addCleanup(pool.stop)
        self.output = io.StringIO()
        for redirect in (contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output)):
            redirect.__enter__()
            self.addCleanup(redirect.__exit__, None, None, None)
        self.build = load(TOOLS / 'build_data.py')

    def manifest(self):
        return json.loads(data_manifest.manifest_path(self.data).read_text())

    def test_failed_corpus_exits_nonzero_and_does_not_save_partial_data(self):
        sys.modules['opus_stream'].pairs = lambda *args: (_ for _ in ()).throw(OSError('download failed'))
        with patch.object(sys, 'argv', ['build_data.py', 'es']):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(TOOLS / 'build_data.py'), run_name='__main__')
        self.assertEqual(raised.exception.code, 1)
        self.assertFalse((self.data / 'clean' / 'es.tsv').exists())
        self.assertEqual(self.manifest(), {'languages': ['es'], 'complete': False})

    def test_success_records_the_requested_language_set(self):
        self.assertEqual(self.build.main(['es']), 0)
        self.assertEqual(self.manifest(), {'languages': ['es'], 'complete': True})
        self.assertEqual(data_manifest.clean_languages(self.data), ['es'])

    def test_no_usable_pairs_is_a_failed_language(self):
        sys.modules['opus_stream'].pairs = lambda *args: []
        self.assertEqual(self.build.main(['es']), 1)
        self.assertFalse((self.data / 'clean' / 'es.tsv').exists())

    def test_retry_does_not_forget_other_requested_languages(self):
        data_manifest.write_manifest(self.data, ['es', 'fr'], False)
        self.assertEqual(self.build.main(['es']), 1)
        self.assertEqual(self.manifest()['languages'], ['es', 'fr'])
        (self.data / 'clean' / 'fr.tsv').write_text('bonjour\thello\tOpenSubtitles\n')
        self.assertEqual(self.build.main(['fr']), 0)
        self.assertEqual(data_manifest.clean_languages(self.data), ['es', 'fr'])

    def test_prepare_rejects_an_incomplete_request_before_loading_tokenizers(self):
        data_manifest.write_manifest(self.data, ['es', 'fr'], False)
        (self.data / 'clean' / 'es.tsv').write_text('hola\thello\tOpenSubtitles\n')
        with patch.dict(os.environ, {'NCMT_DATA': str(self.data)}):
            prepare = load(TOOLS / 'prepare.py')
        for operation in (prepare.spm_train, prepare.encode):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                operation()

    def test_deleted_or_empty_requested_language_is_rejected(self):
        data_manifest.write_manifest(self.data, ['es', 'fr'], True)
        (self.data / 'clean' / 'es.tsv').write_text('hola\thello\tOpenSubtitles\n')
        for empty_file in (False, True):
            if empty_file:
                (self.data / 'clean' / 'fr.tsv').touch()
            with self.assertRaisesRegex(ValueError, 'missing/empty=.*fr'):
                data_manifest.clean_languages(self.data)

    def test_legacy_folder_must_contain_all_33_languages(self):
        (self.data / 'clean' / 'es.tsv').write_text('hola\thello\tOpenSubtitles\n')
        with self.assertRaisesRegex(ValueError, 'missing/empty'):
            data_manifest.clean_languages(self.data)

    def test_training_rejects_encoded_data_missing_a_requested_language(self):
        data_manifest.write_manifest(self.data, ['es', 'fr'], True)
        (self.data / 'bin').mkdir()
        meta = self.data / 'bin' / 'meta.json'
        meta.write_text(json.dumps({'langs': ['es'], 'stats': [['es', 1, 2, 2]]}))
        with self.assertRaisesRegex(ValueError, 'missing=.*fr'):
            data_manifest.validate_encoded(self.data)
        meta.write_text(json.dumps({'langs': ['es', 'fr', 'kd-es'],
                                    'stats': [['es', 1, 2, 2], ['fr', 1, 2, 2], ['kd-es', 1, 2, 2]]}))
        data_manifest.validate_encoded(self.data)


if __name__ == '__main__':
    unittest.main(verbosity=2)
