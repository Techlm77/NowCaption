#!/usr/bin/env python3
"""The transcript store (1.1.4): the session's lines live in an unnamed temporary file in the
cache folder, or in memory when that cannot be written. No models or GTK.
"""
import errno
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine

TS = 1_700_000_000.0


class TranscriptStoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / 'nowcaption'

    def test_lines_go_to_a_private_file_without_a_name(self):
        store = engine.TranscriptStore(self.folder)
        store.append(TS, 'Hello there.')
        store.append(TS + 61, 'Café, naïve — 字幕.')
        self.assertEqual(store.lines(), [engine.stamp(TS) + 'Hello there.',
                                         engine.stamp(TS + 61) + 'Café, naïve — 字幕.'])
        self.assertEqual(store.count, 2)
        self.assertIsNone(store.memory)
        info = os.fstat(store.file.fileno())
        self.assertEqual(info.st_nlink, 0)  # no name: nothing else can open it, and it goes away with the app
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)

    def test_clear_frees_the_old_text_on_disk(self):
        store = engine.TranscriptStore(self.folder)
        store.append(TS, 'Private words.')
        store.clear()
        self.assertEqual(os.fstat(store.file.fileno()).st_size, 0)
        self.assertEqual((store.lines(), store.count), ([], 0))
        store.append(TS, 'New words.')
        self.assertEqual(store.lines(), [engine.stamp(TS) + 'New words.'])

    def test_cache_folder_follows_xdg_cache_home(self):
        with patch.dict(os.environ, XDG_CACHE_HOME=str(self.folder.parent)):
            self.assertEqual(engine.cache_dir(), self.folder)
            store = engine.TranscriptStore()
        self.assertIsNotNone(store.file)

    def test_an_unusable_cache_folder_keeps_the_transcript_in_memory(self):
        self.folder.parent.mkdir(exist_ok=True)
        self.folder.write_text('a file where the folder should be')
        store = engine.TranscriptStore(self.folder)
        self.assertIsNone(store.file)
        store.append(TS, 'Still kept.')
        self.assertEqual(store.lines(), [engine.stamp(TS) + 'Still kept.'])
        store.clear()
        self.assertEqual(store.lines(), [])

    def test_a_failed_write_moves_every_line_to_memory(self):
        store = engine.TranscriptStore(self.folder)
        store.append(TS, 'one')
        real_pwrite, calls = os.pwrite, []

        def full_disk(fd, data, offset):
            calls.append(offset)
            if len(calls) == 2:
                real_pwrite(fd, data[:3], offset)  # a partial line must not appear
                raise OSError(errno.ENOSPC, 'No space left on device')
            return real_pwrite(fd, data, offset)

        with patch.object(engine.transcript.os, 'pwrite', full_disk):
            store.append(TS, 'two')
            store.append(TS, 'three')
        self.assertIsNone(store.file)
        self.assertEqual(store.lines(), [engine.stamp(TS) + w for w in ('one', 'two', 'three')])
        self.assertEqual(store.count, 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
