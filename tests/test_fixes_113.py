#!/usr/bin/env python3
"""Regressions for the 1.1.3 memory fix, without native models or GTK.

sherpa-onnx 1.13.8 leaks a copy of the recognizer's whole text each time Python reads
result.text. The app read it 25 times a second, so memory grew by about 800 MB per hour
of speech. tests/test_memory.py checks the real recognizer.
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from nowcaption import engine
import test_reliability as fixtures

SPEECH = np.full(640, 1000, dtype=np.int16).tobytes()  # one 40 ms block
SILENCE = np.zeros(640, dtype=np.int16).tobytes()


class ChunkedStream:
    def __init__(self, rec):
        self.rec, self.text, self.queued, self.waiting = rec, '', '', 0

    def accept_waveform(self, rate, samples):
        self.waiting += 1
        if np.any(samples) and self.rec.script:
            self.queued += self.rec.script.pop(0)


class ChunkedRecognizer:
    """Like the real model fed 40 ms blocks: a decode runs only every few blocks (every
    second block here), and only a decode changes the result."""

    def __init__(self, script=()):
        self.script, self.reads = list(script), 0

    def create_stream(self):
        return ChunkedStream(self)

    def is_ready(self, s):
        return s.waiting >= 2

    def decode_stream(self, s):
        s.waiting -= 2
        s.text, s.queued = s.text + s.queued, ''

    def get_result(self, s):
        self.reads += 1
        return s.text

    def reset(self, s):
        s.text = s.queued = ''


class ResultReadTests(unittest.TestCase):
    def test_the_text_is_read_only_after_a_decode(self):
        rec = ChunkedRecognizer(['hello ', 'there ', 'again ', 'and ', 'more '] * 2)
        cap = engine.Captioner(rec)
        for _ in range(20):
            cap.feed(SPEECH)
        self.assertEqual(rec.reads, 1 + 10)  # the first block reads the empty stream once
        cap.flush()
        self.assertIn('Hello there again and more hello there again and more', cap.transcript())

    def test_clear_does_not_show_the_old_streams_text_before_the_new_one_decodes(self):
        rec = ChunkedRecognizer(['before ', 'clear '])
        updates = []
        cap = engine.Captioner(rec, on_update=updates.append)
        for _ in range(4):
            cap.feed(SPEECH)
        self.assertIn('before clear', updates[-1].lower())
        cap.clear()
        cap.feed(SPEECH)  # buffered, not decoded yet
        self.assertEqual(updates[-1], '')
        self.assertEqual(cap.transcript(), '')

    def test_text_reset_at_a_natural_break_is_read_again_without_repeating(self):
        words = [f'word{i:04d}' + 'x' * 50 + ' ' for i in range(400)]  # over 20,000 characters
        rec = ChunkedRecognizer(words + ['fresh '])
        cap = engine.Captioner(rec)
        for _ in range(800):
            cap.feed(SPEECH)
        for _ in range(30):  # a pause commits the line, then the stream is reset
            cap.feed(SILENCE)
        cap.feed(SPEECH)  # one buffered block after the reset: no decode yet
        cap.feed(SPEECH)
        cap.flush()
        transcript = cap.transcript().lower()
        self.assertEqual(transcript.count('word0000'), 1)
        self.assertEqual(transcript.count('word0399'), 1)
        self.assertEqual(transcript.count('fresh'), 1)


class LeakFreeRecognizerTests(unittest.TestCase):
    def test_load_recognizer_rebuilds_the_text_from_tokens_instead_of_result_text(self):
        class Result:
            tokens = [' the', ' quick', ' br', 'own', ' fox ']

            @property
            def text(self):
                raise AssertionError('result.text leaks in sherpa-onnx 1.13.8')

        class OnlineRecognizer:  # the parts of sherpa_onnx.OnlineRecognizer used here
            @classmethod
            def from_transducer(cls, **config):
                self = cls.__new__(cls)
                self.recognizer, self.config = types.SimpleNamespace(get_result=lambda s: Result()), config
                return self

            def get_result(self, s):
                return self.recognizer.get_result(s).text.strip()

        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / engine.ASR_DIR
            folder.mkdir()
            for name in engine.ASR_FILES:
                (folder / name).write_text('x')
            fake = types.SimpleNamespace(OnlineRecognizer=OnlineRecognizer)
            with patch.dict(sys.modules, sherpa_onnx=fake), patch.dict(os.environ, NOWCAPTION_MODELS=d):
                rec = engine.load_recognizer()
        self.assertIsInstance(rec, OnlineRecognizer)
        self.assertEqual(rec.config['decoding_method'], 'greedy_search')
        self.assertEqual(rec.get_result(None), 'the quick brown fox')


class WorkerStillRecognizesTests(unittest.TestCase):
    def test_the_worker_captions_with_a_recognizer_that_decodes_every_second_block(self):
        events = fixtures.Events()
        worker = engine.RecognitionWorker(events, load=lambda: (ChunkedRecognizer(['hello ', 'world ']), None))
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        for _ in range(4):
            worker.feed(0, SPEECH)
        self.assertIn('hello world', events.snapshot(worker).lower())
        self.assertIn(('recognition-ok', 0), events.seen)


if __name__ == '__main__':
    unittest.main(verbosity=2)
