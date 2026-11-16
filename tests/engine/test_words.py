#!/usr/bin/env python3
"""The word list (issue #5): fixes for words the model mishears, and the captions and transcript
showing them. words.py and the real Captioner with controlled models; no GTK.
"""
import tempfile
import unittest
from pathlib import Path

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine, words
from support.fakes import SILENCE, SPEECH, FakePunct, StrippedRecognizer

NVIDIA = [('in video', 'Nvidia'), ('and video', 'Nvidia')]


class WordFixTests(unittest.TestCase):
    def test_a_misheard_phrase_is_replaced_only_as_whole_words(self):
        fixes = words.WordFixes(NVIDIA)
        self.assertEqual(fixes.heard('i bought an in video card'), 'i bought an nvidia card')
        self.assertEqual(fixes.heard('and video is faster'), 'nvidia is faster')
        self.assertEqual(fixes.heard('clips in videos'), 'clips in videos')  # "videos" is another word
        self.assertEqual(fixes.heard('within video'), 'within video')

    def test_spelling_keeps_punctuation_and_possessives(self):
        fixes = words.WordFixes([('', 'NVIDIA'), ('', 'KDE Plasma')])
        out = fixes.spelled('I like nvidia. "Nvidia\'s" drivers, kde plasma!'.split())  # noqa: SIM905 - reads as text
        self.assertEqual(out, ['I', 'like', 'NVIDIA.', '"NVIDIA\'s"', 'drivers,', 'KDE', 'Plasma!'])

    def test_a_fix_never_repeats_words_that_are_already_right(self):
        fixes = words.WordFixes([('visual studio', 'Visual Studio Code')])
        self.assertEqual(fixes.heard('open visual studio code'), 'open visual studio code')
        self.assertEqual(fixes.heard('open visual studio now'), 'open visual studio code now')

    def test_empty_fixes_are_ignored(self):
        self.assertFalse(words.WordFixes([('something', '  ')]))
        self.assertFalse(words.WordFixes())

    def test_the_list_is_saved_and_read_back(self):
        path = Path(tempfile.mkdtemp()) / 'nowcaption' / 'words.json'
        words.save_words(NVIDIA + [('', 'KDE')], path)
        self.assertEqual(words.load_words(path), NVIDIA + [('', 'KDE')])
        path.write_text('{not json')
        self.assertEqual(words.load_words(path), [])  # a damaged file never stops NowCaption
        self.assertEqual(words.load_words(path.with_name('missing.json')), [])


class CaptionerWordTests(unittest.TestCase):
    def say(self, script, fixes=()):
        rec = StrippedRecognizer(script)
        self.updates = []
        cap = engine.Captioner(rec, FakePunct(), on_update=self.updates.append, fixes=words.WordFixes(fixes))
        for _ in script:
            cap.feed(SPEECH)
        for _ in range(12):
            cap.feed(SILENCE)
        return cap

    def test_captions_and_transcript_show_the_fix(self):
        cap = self.say(['i', ' bought', ' an', ' in', ' video', ' card'], NVIDIA)
        self.assertIn('Nvidia card', self.updates[-1])
        self.assertTrue(cap.transcript().endswith('I bought an Nvidia card.'))

    def test_words_still_being_spoken_are_fixed_too(self):
        rec = StrippedRecognizer(['the', ' in', ' video'])
        updates = []
        cap = engine.Captioner(rec, FakePunct(), on_update=updates.append, fixes=words.WordFixes(NVIDIA))
        for _ in range(3):
            cap.feed(SPEECH)
        self.assertEqual(updates[-1], 'The Nvidia.')
        self.assertTrue(cap.transcript().endswith('The Nvidia.'))

    def test_a_new_list_applies_to_the_live_passage(self):
        rec = StrippedRecognizer(['my', ' nvidia'])
        updates = []
        cap = engine.Captioner(rec, FakePunct(), on_update=updates.append)
        for _ in range(2):
            cap.feed(SPEECH)
        self.assertEqual(updates[-1], 'My nvidia.')
        cap.set_fixes(words.WordFixes([('', 'NVIDIA')]))
        self.assertEqual(updates[-1], 'My NVIDIA.')

    def test_without_a_list_nothing_changes(self):
        cap = self.say(['i', ' bought', ' an', ' in', ' video', ' card'])
        self.assertTrue(cap.transcript().endswith('I bought an in video card.'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
