#!/usr/bin/env python3
"""Starting NowCaption at login (issue #5), outside the Flatpak, where it writes its own
autostart file. Needs PyGObject.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from support.gtk import SKIP, nc

if nc:  # GTK is there
    from nowcaption.desktop import autostart
else:
    autostart = None


@unittest.skipIf(autostart is None, globals().get('SKIP', ''))
class AutostartTests(unittest.TestCase):
    def test_exec_quotes_paths_with_spaces(self):
        self.assertEqual(autostart.desktop_exec(['/home/me/My Apps/NowCaption.AppImage']),
                         '"/home/me/My Apps/NowCaption.AppImage"')
        self.assertEqual(autostart.desktop_exec(['nowcaption']), 'nowcaption')
        self.assertEqual(autostart.desktop_exec(['a$b']), '"a\\$b"')

    def test_outside_the_flatpak_a_file_is_written_and_removed(self):
        with patch.dict(os.environ, XDG_CONFIG_HOME=tempfile.mkdtemp(), APPIMAGE='/opt/Now Caption.AppImage'), \
                patch.object(autostart, 'in_flatpak', lambda: False):
            auto = autostart.Autostart(None, 'io.github.Techlm77.NowCaption')
            results = []
            auto.set(True, lambda on, message: results.append((on, message)))
            text = auto.path().read_text()
            self.assertIn('Exec="/opt/Now Caption.AppImage"\n', text)
            auto.set(False, lambda on, message: results.append((on, message)))
            self.assertFalse(auto.path().exists())
            self.assertEqual(results, [(True, ''), (False, '')])


if __name__ == '__main__':
    unittest.main(verbosity=2)
