#!/usr/bin/env python3
"""The benchmark's scoring (benchmark/e2e.py, 1.0.3) and its full-history trace (1.0.4): a word
is scored only in its own clip, and the trace keeps every word after the caption window
scrolls. The real worker and Captioner with controlled models.
"""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support.fakes import SILENCE, SPEECH, Events, FakeRecognizer

ROOT = Path(__file__).resolve().parents[2]  # the repository


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
                 patch.object(self.e2e.engine, 'Capture', Capture), \
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
