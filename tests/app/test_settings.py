#!/usr/bin/env python3
"""The settings file: hand-edited values are brought back into range, and a file that cannot be
written does not stop NowCaption. Needs PyGObject (GLib's key files).
"""
import tempfile
import unittest
from pathlib import Path

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import style
from support.gtk import SKIP, nc

if nc:  # GTK is there
    from nowcaption import settings


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = settings.Settings()
        self.settings.path = Path(tempfile.mkdtemp()) / 'settings.ini'

    def test_hand_edited_values_are_brought_back_into_range(self):
        kf = self.settings.kf
        kf.set_integer('main', 'size', 0)
        kf.set_double('main', 'opacity', 5.0)
        kf.set_integer('main', 'rows', -3)
        kf.set_integer('main', 'width', 5)
        kf.set_string('main', 'source', 'speakers')
        kf.set_string('main', 'colour', 'purple')
        kf.set_string('main', 'word_effect', 'sparkles')
        kf.set_string('main', 'word_speed', 'warp')
        self.assertEqual(self.settings.get('colour'), 'white')
        self.assertEqual((self.settings.get('word_effect'), self.settings.get('word_speed')), ('fade', 'normal'))
        self.assertEqual(self.settings.get('size'), style.SIZES[0])
        self.assertEqual(self.settings.get('opacity'), 1.0)
        self.assertEqual(self.settings.get('rows'), 1)
        self.assertEqual(self.settings.get('width'), style.MIN_WIDTH)
        self.assertEqual(self.settings.get('source'), 'desktop')

    def test_default_width_and_valid_values_are_kept(self):
        self.assertEqual(self.settings.get('width'), -1)
        self.settings.set('source', 'mic')
        self.settings.set('size', 42)
        self.assertEqual((self.settings.get('source'), self.settings.get('size')), ('mic', 42))

    def test_the_text_colour_is_remembered_after_a_restart(self):
        self.settings.set('colour', 'lime')
        again = settings.Settings()  # as NowCaption reads it the next time it starts
        again.path = self.settings.path
        again.kf.load_from_file(str(again.path), nc.GLib.KeyFileFlags.NONE)
        self.assertEqual(again.get('colour'), 'lime')

    def test_a_settings_file_that_cannot_be_written_does_not_raise(self):
        blocker = Path(tempfile.mkdtemp()) / 'not-a-folder'
        blocker.write_text('')
        self.settings.path = blocker / 'settings.ini'
        self.settings.set('source', 'mic')  # must not raise
        self.assertEqual(self.settings.get('source'), 'mic')


if __name__ == '__main__':
    unittest.main(verbosity=2)
