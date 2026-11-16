#!/usr/bin/env python3
"""Reading the recognizer's text (1.1.3). sherpa-onnx 1.13.8 leaks a copy of the recognizer's
whole text each time Python reads result.text. The app read it 25 times a second, so memory
grew by about 800 MB per hour of speech. tests/test_memory.py checks the real recognizer.

The text is also where the copies of a word the model repeats on one frame are dropped:
SameFrameCopyCaptionTests run v4's recorded tokens through load_recognizer() and the Captioner
(test_tokens.py tests the filter itself). No native models or GTK.
"""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support import fakes as fixtures

SPEECH = np.full(640, 1000, dtype=np.int16).tobytes()  # one 40 ms block
SILENCE = np.zeros(640, dtype=np.int16).tobytes()
RECORDED = json.loads((Path(__file__).parents[1] / 'fixtures' / 'repeats-v4.json').read_text())


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
            timestamps = [0.08, 0.16, 0.24, 0.24, 0.32]

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


class NativeResult:
    """The parts of sherpa-onnx 1.13.8's OnlineRecognizerResult that the app reads."""

    def __init__(self, tokens, timestamps):
        self.tokens, self.timestamps = tokens, timestamps

    @property
    def text(self):
        raise AssertionError('result.text leaks in sherpa-onnx 1.13.8')


class FramedStream:
    def __init__(self, rec):
        self.rec, self.tokens, self.times, self.queued, self.waiting = rec, [], [], [], 0

    def accept_waveform(self, rate, samples):
        self.waiting += 1
        if np.any(samples) and self.rec.frames:
            self.queued += self.rec.frames.pop(0)


class FramedOnlineRecognizer:
    """sherpa_onnx.OnlineRecognizer as load_recognizer() builds on it: each 40 ms block of speech
    brings the next scripted frame's (token, time) pairs, and a decode, every second block as with
    the real model, adds them to the result."""

    @classmethod
    def from_transducer(cls, **config):
        self = cls.__new__(cls)
        self.frames, self.resets = [], 0
        self.recognizer = types.SimpleNamespace(get_result=lambda s: NativeResult(list(s.tokens), list(s.times)))
        return self

    def create_stream(self):
        return FramedStream(self)

    def is_ready(self, s):
        return s.waiting >= 2

    def decode_stream(self, s):
        s.waiting -= 2
        for token, time in s.queued:
            s.tokens.append(token)
            s.times.append(time)
        s.queued = []

    def reset(self, s):
        self.resets += 1
        s.tokens, s.times = [], []


def frames(tokens, times):
    """Tokens grouped by the frame they were emitted on."""
    out = []
    for token, time in zip(tokens, times):
        if out and out[-1][-1][1] == time:
            out[-1].append((token, time))
        else:
            out.append([(token, time)])
    return out


class SameFrameCopyCaptionTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / engine.ASR_DIR
            folder.mkdir()
            for name in engine.ASR_FILES:
                (folder / name).write_text('x')
            fake = types.SimpleNamespace(OnlineRecognizer=FramedOnlineRecognizer)
            with patch.dict(sys.modules, sherpa_onnx=fake), patch.dict(os.environ, NOWCAPTION_MODELS=d):
                self.rec = engine.load_recognizer()
        self.updates = []
        self.cap = engine.Captioner(self.rec, on_update=self.updates.append, keep_raw=True)

    def say(self, case, after=0.0):
        """Speak a recorded case frame by frame, its times moved on by `after` seconds, then pause
        long enough to finish the line."""
        self.rec.frames += frames(RECORDED[case]['tokens'], [t + after for t in RECORDED[case]['times']])
        while self.rec.frames:
            self.cap.feed(SPEECH)
        for _ in range(30):
            self.cap.feed(SILENCE)

    def test_a_word_the_model_repeats_on_one_frame_never_reaches_the_captions(self):
        self.say('okay said 6 times, varied recordings')  # 33 copies came out
        self.assertEqual(self.cap.raw_text(), ' '.join(['okay'] * 6))
        self.assertTrue(self.updates)
        self.assertFalse([u for u in self.updates if u.lower().count('okay') > 6])
        self.assertEqual(self.cap.transcript().split('] ', 1)[1], 'Okay' + ' okay' * 5)

    def test_a_word_held_back_as_a_possible_copy_shows_once_it_is_not_one(self):
        # " p" on the frame where "plus" ended may become a copy of it, until "ush" arrives, and
        # the pause in between finishes the line: the next one starts where "plus" ended.
        self.rec.frames += [[(' plus', 2.0), (' p', 2.0)]]
        for _ in range(4):  # speech that brings no new tokens yet
            self.cap.feed(SPEECH)
        self.assertEqual(self.updates[-1].lower(), 'plus')
        for _ in range(30):
            self.cap.feed(SILENCE)
        self.rec.frames += [[('ush', 2.08)], [(' on', 2.16)]]
        self.say('no said 3 times, 300 ms apart')
        self.assertEqual(self.cap.raw_text(), 'plus push on no no no')
        self.assertFalse([u for u in self.updates if 'plus p' in u.lower() and 'plus push' not in u.lower()])

    def test_copies_are_still_dropped_after_the_stream_is_reset_at_a_natural_break(self):
        long_words = [f' word{i:04d}' + 'x' * 50 for i in range(400)]  # over 20,000 characters
        self.rec.frames += [[(w, 0.08 * i)] for i, w in enumerate(long_words)]
        self.say('okay said 6 times, varied recordings', after=0.08 * len(long_words))
        self.assertEqual(self.rec.resets, 1)
        self.say('okay said 6 times, varied recordings')  # a reset stream's result starts empty
        raw = self.cap.raw_text()
        self.assertEqual(raw.count('word0000'), 1)
        self.assertEqual(raw.count('word0399'), 1)
        self.assertEqual(raw.split().count('okay'), 12)


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
