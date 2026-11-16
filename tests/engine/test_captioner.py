#!/usr/bin/env python3
"""The Captioner: how recognised words become caption lines and the exported transcript: where
lines end, timestamps, the warm-up silence of each new stream, the memory a long session keeps,
and exports that match the captions. Fake models; no GTK, audio server or downloaded models.

TimestampTests come from the reliability review, BoundedCaptionerTests from 1.1.4,
WordBoundaryTests and TranscriptFormattingTests from 1.1.5, WarmUpTests from 1.1.6.
"""
import time
import unittest
from unittest.mock import patch

import numpy as np

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support import fakes as fixtures
from support.fakes import SILENCE, SPEECH, Events, FakePunct, FakeRecognizer, FakeStream, StrippedRecognizer

BLOCK = np.full(640, 1000, dtype=np.int16).tobytes()  # 40 ms, as the capture delivers it


def talk(cap, words):
    """Speak some words, then pause long enough to finish the line."""
    for _ in range(words):
        cap.feed(fixtures.SPEECH)
    for _ in range(9):
        cap.feed(fixtures.SILENCE)


def transcript_words(text):
    """The exported passage without its per-line timestamps."""
    return ' '.join(line.split('] ', 1)[1] for line in text.splitlines())


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


class BoundedCaptionerTests(unittest.TestCase):
    def test_a_long_session_keeps_only_what_the_caption_box_needs(self):
        script = [f'word{i} ' for i in range(3000)]
        updates = []
        cap = engine.Captioner(fixtures.FakeRecognizer(script), on_update=updates.append)
        for _ in range(500):
            talk(cap, 6)
        self.assertEqual(cap.count, 500)
        self.assertLessEqual(len(cap.recent), cap.LIVE_LINES + 1)
        self.assertLessEqual(len(cap.shown), cap.DISPLAY_LINES)
        lines = cap.transcript().splitlines()
        self.assertEqual(len(lines), 500)
        self.assertTrue(lines[0].endswith('Word0 word1 word2 word3 word4 word5'))
        self.assertIn('word2999', lines[-1])
        self.assertTrue(updates[-1].endswith('word2999'))
        self.assertIsNone(cap.raw)  # raw history is kept only for benchmark tracing

    def test_without_caption_updates_memory_is_bounded_too(self):
        cap = engine.Captioner(fixtures.FakeRecognizer([f'word{i} ' for i in range(1000)]))
        for _ in range(300):
            talk(cap, 3)
        self.assertEqual(cap.count, 300)
        self.assertLessEqual(len(cap.recent), cap.DISPLAY_LINES + cap.LIVE_LINES + 1)
        self.assertEqual(len(cap.transcript().splitlines()), 300)

    def test_recovery_continues_the_same_transcript_file(self):
        events = fixtures.Events()
        worker = engine.RecognitionWorker(events, load=lambda: (fixtures.FakeRecognizer(['before ', 'after ']), None))
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        store = worker.captioner.store
        worker.feed(0, fixtures.SPEECH)
        events.snapshot(worker)
        worker.restart()
        worker.feed(0, fixtures.SPEECH)
        self.assertIs(worker.captioner.store, store)
        text = events.snapshot(worker)
        self.assertEqual(text.lower().count('before'), 1)
        self.assertIn('after', text.lower())

    def test_tracing_keeps_the_complete_raw_text(self):
        events = fixtures.Events()
        traces = []
        worker = engine.RecognitionWorker(events, load=lambda: (fixtures.FakeRecognizer(['one ', 'two ']), None),
                                          on_trace=traces.append)
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        for _ in range(2):
            worker.feed(0, fixtures.SPEECH)
        events.snapshot(worker)
        self.assertEqual(traces[-1], 'one two')
        with self.assertRaises(RuntimeError):
            engine.Captioner(fixtures.FakeRecognizer()).raw_text()


class WordBoundaryTests(unittest.TestCase):
    def make(self, prefix):
        self.rec = StrippedRecognizer([prefix])
        self.updates = []
        return engine.Captioner(self.rec, FakePunct(), on_update=self.updates.append)

    def wait_without_new_tokens(self, cap):
        for _ in range(10):
            cap.feed(SPEECH)  # sound continues; only the text is unchanged

    def test_a_pause_over_background_sound_finishes_the_whole_line(self):
        # A microphone or a video with music is never digitally silent: holding the last
        # word until digital silence moved every line's last word onto the next line.
        cap = self.make('we understand')
        cap.feed(SPEECH)
        self.wait_without_new_tokens(cap)
        self.assertEqual(cap.count, 1)
        self.assertFalse(cap.partial.strip())
        self.rec.script.append(' today')
        cap.feed(SPEECH)
        cap.flush()
        lines = [line.split('] ', 1)[1] for line in cap.transcript().splitlines()]
        self.assertEqual(lines, ['We understand', 'today.'])  # not ['We', 'understand today.']

    def test_real_digital_silence_can_finish_a_single_word(self):
        cap = self.make('hello')
        cap.feed(SPEECH)
        for _ in range(9):
            cap.feed(SILENCE)
        self.assertEqual(cap.count, 1)
        self.assertFalse(cap.partial.strip())
        self.assertEqual(transcript_words(cap.transcript()), 'Hello.')

    def test_length_limit_does_not_insert_spaces_inside_a_long_word(self):
        word = 'x' * (engine.MAX_LINE + 20)
        cap = self.make(word)
        cap.feed(SPEECH)
        self.wait_without_new_tokens(cap)
        self.assertEqual(cap.count, 0)
        self.rec.script.append(' end')
        cap.feed(SPEECH)
        cap.flush()
        self.assertEqual(transcript_words(cap.transcript()).lower(), word + ' end.')


class TranscriptFormattingTests(unittest.TestCase):
    def make(self, script, punctuation=None):
        self.rec, self.updates = FakeRecognizer(script), []
        return engine.Captioner(self.rec, punctuation or FakePunct(), on_update=self.updates.append)

    def test_two_lines_export_the_same_sentence_as_the_caption_passage(self):
        cap = self.make(['hello ', 'world '])
        cap.feed(SPEECH)
        cap.feed(SILENCE * 12)
        cap.feed(SPEECH)
        cap.flush()
        self.assertEqual(self.updates[-1], 'Hello world.')
        self.assertEqual(transcript_words(cap.transcript()), self.updates[-1])

    def test_copying_does_not_freeze_punctuation_before_the_next_words_arrive(self):
        cap = self.make(['hello ', 'world '])
        cap.feed(SPEECH)
        cap.feed(SILENCE * 12)
        before = cap.transcript()
        cap.feed(SPEECH)
        after = cap.transcript()
        self.assertEqual(transcript_words(before), 'Hello.')
        self.assertEqual(transcript_words(after), 'Hello world.')
        self.assertEqual(before.split(']')[0], after.split(']')[0])

    def test_punctuation_whitespace_changes_do_not_drop_exported_words(self):
        class ExpandedPunctuation(FakePunct):
            def add_punctuation_with_case(self, text):
                return super().add_punctuation_with_case(text.replace('cannot', 'can not'))

        cap = self.make(['cannot ', 'go '], ExpandedPunctuation())
        cap.feed(SPEECH)
        cap.feed(SILENCE * 12)
        cap.feed(SPEECH)
        cap.flush()
        self.assertEqual(self.updates[-1], 'Can not go.')
        self.assertEqual(transcript_words(cap.transcript()), self.updates[-1])

    def test_unchanged_exports_reuse_the_visible_punctuation(self):
        class ChangingPunctuation:
            calls = 0

            def add_punctuation_with_case(self, text):
                self.calls += 1
                return text.capitalize() + ('.' if self.calls % 2 else '?')

        punct = ChangingPunctuation()
        cap = self.make(['hello '], punct)
        cap.feed(SPEECH)
        calls = punct.calls
        for _ in range(3):
            self.assertEqual(transcript_words(cap.transcript()), self.updates[-1])
        self.assertEqual(punct.calls, calls)

    def test_settled_punctuation_survives_scrolling_and_every_word_is_exported_once(self):
        cap = self.make([f'line{i} word ' for i in range(200)])
        with patch.object(engine.Captioner, 'DISPLAY_LINES', 3):
            for _ in range(200):
                cap.feed(SPEECH)
                cap.feed(SILENCE * 12)
            lines = cap.transcript().splitlines()
            self.assertEqual(len(lines), 200)
            self.assertEqual(transcript_words('\n'.join(lines[-3:])), self.updates[-1])
            for i in range(200):
                self.assertEqual(transcript_words(lines[i]).lower().split()[0], f'line{i}')
            self.assertLessEqual(len(cap.recent), cap.LIVE_LINES + 1)
            self.assertEqual(cap.store.count, cap.settled)

    def test_recovery_keeps_pending_lines_and_the_same_exported_punctuation(self):
        events = Events()
        rec = FakeRecognizer(['hello ', 'world ', 'again '])
        worker = engine.RecognitionWorker(events, load=lambda: (rec, FakePunct()))
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        worker.feed(0, SPEECH)
        worker.feed(0, SILENCE * 12)
        worker.feed(0, SPEECH)
        before = events.snapshot(worker)
        rec.fail = True
        worker.feed(0, SPEECH)
        events.wait('error')
        rec.fail = False
        worker.restart()
        self.assertEqual(events.snapshot(worker), before)
        worker.feed(0, SPEECH)
        after = events.snapshot(worker)
        self.assertEqual(transcript_words(after), 'Hello world again.')
        worker.clear(1)
        self.assertEqual(events.snapshot(worker), '')


class WarmUpTests(unittest.TestCase):
    # NowCaption's own model (1.1.6) shows words up to 80 ms later when speech starts on a
    # stream's very first sample, so each new stream gets silence in front of its first block.
    class Rec(FakeRecognizer):
        def __init__(self, script):
            super().__init__(script)
            self.accepted = []

        def create_stream(self):
            rec = self

            class Stream(FakeStream):
                def accept_waveform(self, rate, samples):
                    super().accept_waveform(rate, samples)
                    rec.accepted.append(np.asarray(samples))
            return Stream(self)

    def test_the_first_block_of_a_stream_comes_after_the_warm_up_silence(self):
        rec = self.Rec(['hello '] * 10)
        cap = engine.Captioner(rec)
        cap.feed(BLOCK)
        cap.feed(BLOCK)
        warm = int(engine.WARMUP_S * engine.RATE)
        self.assertEqual(len(rec.accepted), 2, 'the silence goes in the same call as the first block')
        first, second = rec.accepted
        self.assertEqual(len(first), warm + 640)
        self.assertFalse(first[:warm].any())
        self.assertTrue(first[warm:].all())
        self.assertEqual(len(second), 640)

    def test_clear_starts_a_new_stream_that_is_warmed_up_again(self):
        rec = self.Rec(['hello '] * 10)
        cap = engine.Captioner(rec)
        cap.feed(BLOCK)
        cap.clear()
        cap.feed(BLOCK)
        self.assertEqual([len(x) for x in rec.accepted], [int(engine.WARMUP_S * engine.RATE) + 640] * 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
