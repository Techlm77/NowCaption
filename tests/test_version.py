#!/usr/bin/env python3
"""The version is written in three places; a release must update all of them together.

The release workflow names the .flatpak files from pyproject.toml, the About dialog shows
__init__.py, and software centres show the newest <release> in the metainfo file.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_pyproject_package_and_metainfo_agree(self):
        pyproject = re.search(r'^version = "(.+)"', (ROOT / 'pyproject.toml').read_text(), re.M)[1]
        package = re.search(r"__version__ = '(.+)'", (ROOT / 'src/nowcaption/__init__.py').read_text())[1]
        metainfo = re.search(r'<release version="([^"]+)"',
                             (ROOT / 'data/io.github.Techlm77.NowCaption.metainfo.xml').read_text())[1]
        self.assertEqual(package, pyproject, '__init__.py and pyproject.toml disagree')
        self.assertEqual(metainfo, pyproject, 'the newest <release> in the metainfo is not this version')


if __name__ == '__main__':
    unittest.main(verbosity=2)
