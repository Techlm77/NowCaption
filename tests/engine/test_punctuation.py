#!/usr/bin/env python3
"""Punctuation: settled lines keep theirs as new words arrive (1.0.4), and the model runs only
when the passage really changes (1.1.7). The real Captioner with controlled models; no GTK,
audio server or downloaded models.
"""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support import fakes
from support.fakes import FakePunct, StrippedRecognizer

BLOCK = np.full(640, 1000, dtype=np.int16)  # 40 ms of speech, as the capture delivers it
QUIET = np.zeros(640, dtype=np.int16)


class CountingPunct(FakePunct):
    def __init__(self):
        super().__init__()
        self.calls = []

    def add_punctuation_with_case(self, text):
        self.calls.append(text)
        return super().add_punctuation_with_case(text)


class StablePunctuationTests(unittest.TestCase):
    FlakyPunctuation = fakes.FlakyPunctuation  # moves its commas whenever the passage grows

    def test_older_lines_keep_their_punctuation_as_new_words_arrive(self):
        captioner = engine.Captioner(NS(create_stream=lambda: None), self.FlakyPunctuation())
        before = ''
        for n in range(10):
            captioner.add_line(f'line{n} a b c')
            for partial in ('', 'x', 'x y', 'x y z'):
                captioner.partial = partial
                now = captioner.caption_text()
                settled = max(0, captioner.count - captioner.LIVE_LINES)
                frozen = ' '.join(now.split()[:4 * settled])
                self.assertTrue(before.startswith(frozen), (before, now))
                before = now
        self.assertEqual(len(before.split()), 4 * 10 + 3)

    def test_passage_still_holds_only_the_last_display_lines(self):
        captioner = engine.Captioner(NS(create_stream=lambda: None), self.FlakyPunctuation())
        for n in range(10):
            captioner.add_line(f'line{n}')
        with patch.object(engine.Captioner, 'DISPLAY_LINES', 3):
            self.assertEqual(captioner.caption_text().replace(',', '').split(), ['line7', 'line8', 'line9'])


class PunctuationRunTests(unittest.TestCase):
    def captioner(self, script):
        self.shown, self.punct = [], CountingPunct()
        return engine.Captioner(StrippedRecognizer(script), self.punct, on_update=self.shown.append)

    def test_a_word_spelled_out_piece_by_piece_reuses_its_punctuation(self):
        cap = self.captioner(['annie', "'s", ' eyes'])
        for _ in range(3):
            cap.feed(BLOCK)
        self.assertEqual(self.shown, ['Annie.', "Annie's.", "Annie's eyes."])
        self.assertEqual(self.punct.calls, ['annie', "annie's eyes"])

    def test_i_growing_into_another_word_is_punctuated_again(self):
        cap = self.captioner(['i', 't'])
        for _ in range(2):
            cap.feed(BLOCK)
        self.assertEqual(self.shown, ['I.', 'It.'])
        self.assertEqual(self.punct.calls, ['i', 'it'])

    def test_a_finished_line_is_punctuated_properly(self):
        cap = self.captioner(['annie', "'s"])
        for _ in range(2):
            cap.feed(BLOCK)
        for _ in range(int(engine.PAUSE_S / 0.04) + 1):  # the pause ends the line
            cap.feed(QUIET)
        self.assertEqual(cap.count, 1)
        self.assertEqual(self.punct.calls, ['annie', "annie's"])
        self.assertEqual(self.shown[-1], "Annie's.")

    def test_export_punctuates_a_copied_last_word_properly(self):
        cap = self.captioner(['annie', "'s"])
        for _ in range(2):
            cap.feed(BLOCK)
        self.assertTrue(cap.transcript().endswith("Annie's."))
        self.assertEqual(self.punct.calls, ['annie', "annie's"])

    def test_finished_lines_are_not_punctuated_on_their_own_as_well(self):
        cap = self.captioner(['hello', ' there'])
        for _ in range(2):
            cap.feed(BLOCK)
        for _ in range(int(engine.PAUSE_S / 0.04) + 1):
            cap.feed(QUIET)
        self.assertEqual(cap.count, 1)
        self.assertEqual(self.punct.calls, ['hello', 'hello there'])

    def test_a_line_frozen_just_after_a_reused_word_has_full_punctuation(self):
        class ContextPunct(CountingPunct):  # earlier words depend on the last one, as in the real model
            def add_punctuation_with_case(self, text):
                self.calls.append(text)
                return text.upper() if text.endswith('three') else text.capitalize() + '.'

        cap = engine.Captioner(StrippedRecognizer(['one', ' two', ' thr', 'ee']), ContextPunct(),
                               on_update=lambda text: None)
        for speech in (1, 1, 2):
            for _ in range(speech):
                cap.feed(BLOCK)
            for _ in range(int(engine.PAUSE_S / 0.04) + 1):
                cap.feed(QUIET)
        self.assertEqual(cap.count, 3)
        self.assertEqual(cap.transcript().splitlines()[0].split('] ', 1)[1], 'ONE',
                         'the first line froze with the punctuation shown before "three" was complete')

    def test_lines_never_shown_are_punctuated_when_they_settle(self):
        punct = CountingPunct()
        cap = engine.Captioner(StrippedRecognizer(), punct)  # nothing displays captions
        for n in range(cap.DISPLAY_LINES + cap.LIVE_LINES + 2):
            cap.add_line(f'line{n} a b')
        self.assertIn('] Line0 a b.', cap.transcript().splitlines()[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
