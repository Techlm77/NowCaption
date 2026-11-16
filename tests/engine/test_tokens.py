#!/usr/bin/env python3
"""Words the model repeats on one frame. sherpa-onnx's greedy search lets NowCaption's model emit
up to 10 tokens per 80 ms frame, and after a few copies of a word it could emit that word again
and again on one frame, so people repeating a word saw more copies than they said.
engine/tokens.py drops a copy that starts on the frame where the word before it ended.

The tokens and frame times are v4's own, recorded as the app streams audio, for words from AMI
meeting recordings said several times (tests/fixtures/repeats-v4.json; tests/fixtures/README.md
says how they were made). No native models or GTK.
"""
import json
import unittest
from pathlib import Path

import numpy as np

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption.engine import join_tokens

RECORDED = json.loads((Path(__file__).parents[1] / 'fixtures' / 'repeats-v4.json').read_text())
# The pattern v4 emitted for "plus" in a recording of "c plus plus": a copy of several tokens
PLUS = {'tokens': [' c', ' p', 'lu', 's', ' p', 'lu', 's', ' p', 'lu', 's', ' p', 'lu', 's'],
        'times': [10.08, 10.24, 10.24, 10.32, 10.48, 10.48, 10.56, 10.56, 10.56, 10.56, 10.56, 10.56, 10.56]}


def text(name):
    """A recorded case's text, filtered."""
    case = RECORDED[name]
    return join_tokens(case['tokens'], case['times'])


class SameFrameCopyTests(unittest.TestCase):
    def test_a_word_said_six_times_no_longer_shows_47_times(self):
        case = RECORDED['yes said 6 times back to back']
        self.assertEqual(''.join(case['tokens']).split().count('yes'), 47)
        # Eight frames start a "yes": 47 copies become 8 for the 6 said.
        self.assertEqual(text('yes said 6 times back to back'), ' '.join(['yes'] * 8))

    def test_a_runaway_after_words_said_with_pauses_collapses_to_what_was_said(self):
        self.assertEqual(text('yes said 4 times, 150 ms apart'), 'yes yes yes yes')
        self.assertEqual(text('okay said 4 times, 300 ms apart'), 'okay okay okay okay')

    def test_a_runaway_over_varied_recordings_of_a_word_collapses(self):
        # 33 copies came out, ten on each of three frames
        self.assertEqual(text('okay said 6 times, varied recordings'), ' '.join(['okay'] * 6))

    def test_a_copy_made_of_several_tokens_is_dropped_too(self):
        self.assertEqual(join_tokens(PLUS['tokens'], PLUS['times']), 'c plus plus')

    def test_times_that_differ_by_rounding_are_one_frame(self):
        # sherpa-onnx works frame times out in 32-bit floats (3.84 arrives as 3.8399999141693115):
        # one frame must not depend on two of them being equal to the last bit.
        case = RECORDED['okay said 6 times, varied recordings']
        times = [float(np.float32(t)) + 1e-6 * (i % 2) for i, t in enumerate(case['times'])]
        self.assertEqual(join_tokens(case['tokens'], times), ' '.join(['okay'] * 6))


class GenuineRepeatTests(unittest.TestCase):
    def test_a_word_said_again_on_another_frame_is_kept(self):
        # The second "no" is on the frame straight after the first one: 80 ms later is not zero.
        self.assertEqual(text('no said 3 times, 300 ms apart'), 'no no no')
        self.assertEqual(text('hello said 3 times, 300 ms apart'), 'hello hello hello')

    def test_different_words_on_one_frame_are_kept(self):
        self.assertEqual(join_tokens([' in', ' the', ' end'], [1.04, 1.04, 1.04]), 'in the end')
        # Not the last word, so it can no longer grow into a copy of "plus"
        self.assertEqual(join_tokens([' plus', ' p', ' and'], [2.0, 2.0, 2.08]), 'plus p and')

    def test_a_genuine_repeat_on_one_frame_is_lost(self):
        # The known cost: the model put the last two of these four "okay"s on one frame. In 475
        # real recordings with repeated words, one fast "is is" lost an "is" in the same way.
        self.assertEqual(text('okay said 4 times, varied recordings'), 'okay okay okay')


class GrowingTextTests(unittest.TestCase):
    """The Captioner keeps character offsets into the text, so it may only grow as tokens arrive."""

    def test_the_text_only_grows_as_each_token_arrives(self):
        for name, case in [*RECORDED.items(), ('c plus plus', PLUS)]:
            shown = ''
            for n in range(len(case['tokens']) + 1):
                now = join_tokens(case['tokens'][:n], case['times'][:n])
                self.assertTrue(now.startswith(shown), f'{name}: {shown!r} became {now!r} at token {n}')
                shown = now

    def test_a_last_word_that_may_become_a_copy_waits(self):
        tokens, times = [' plus', ' p', 'lu', 's', ' p', 'ush'], [2.0, 2.0, 2.0, 2.0, 2.0, 2.08]
        shown = [join_tokens(tokens[:n], times[:n]) for n in range(1, len(tokens) + 1)]
        self.assertEqual(shown, ['plus', 'plus', 'plus', 'plus', 'plus', 'plus push'])


class EmptyAndResetTests(unittest.TestCase):
    def test_no_tokens_give_no_text(self):
        self.assertEqual(join_tokens([], []), '')

    def test_tokens_without_a_time_each_are_only_joined(self):
        self.assertEqual(join_tokens([' no', ' no'], []), 'no no')
        self.assertEqual(join_tokens([' no', ' no'], [1.04, 1.04, 1.04]), 'no no')

    def test_a_reset_stream_is_judged_on_its_own_tokens(self):
        # After rec.reset() the result holds only the new tokens, so the word before the reset
        # cannot make the first word after it a copy (test_recognizer_text.py resets a stream).
        self.assertEqual(join_tokens([' plus', ' plus'], [10.56, 10.56]), 'plus')
        self.assertEqual(join_tokens([' plus'], [10.56]), 'plus')


if __name__ == '__main__':
    unittest.main(verbosity=2)
