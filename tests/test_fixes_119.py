#!/usr/bin/env python3
"""Transcript history: each session's finished lines are kept in a private file for 7 days,
including the words still being spoken when NowCaption quits.

Runs the real store, Captioner and recognition worker with controlled models. No GTK, audio
server or downloaded models.
"""
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from nowcaption import engine
from test_reliability import Events, FakePunct
from test_fixes_115 import StrippedRecognizer

BLOCK = np.full(640, 1000, dtype=np.int16)
QUIET = np.zeros(640, dtype=np.int16)


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / 'history' / '2026-10-23 14.05.12.txt'
        self.store = engine.TranscriptStore(self.dir / 'cache')
        self.store.set_history(self.path)

    def test_nothing_is_written_until_something_is_said(self):
        self.assertFalse(self.path.exists())

    def test_finished_lines_go_to_a_file_only_the_user_can_read(self):
        self.store.append(time.mktime((2026, 10, 23, 14, 5, 20, 0, 0, -1)), 'Hello there.')
        self.store.append(time.mktime((2026, 10, 23, 14, 5, 25, 0, 0, -1)), 'How are you?')
        self.assertEqual(self.path.read_text(), '[14:05:20] Hello there.\n[14:05:25] How are you?\n')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    def test_clear_also_empties_the_sessions_history(self):
        self.store.append(time.time(), 'Something private.')
        self.store.clear()
        self.store.append(time.time(), 'After the clear.')
        self.assertTrue(self.path.read_text().endswith('After the clear.\n'))
        self.assertNotIn('private', self.path.read_text())

    def test_turning_history_off_stops_writing(self):
        self.store.append(time.time(), 'Kept.')
        self.store.set_history(None)
        self.store.append(time.time(), 'Not kept.')
        self.assertNotIn('Not kept', self.path.read_text())
        self.assertEqual(len(self.store.lines()), 2)  # the session's own transcript still has both

    def test_an_unwritable_history_does_not_stop_captions(self):
        blocker = self.dir / 'not-a-folder'
        blocker.write_text('')
        self.store.set_history(blocker / 'session.txt')
        self.store.append(time.time(), 'Still captioned.')
        self.assertEqual(len(self.store.lines()), 1)
        self.assertEqual(self.store.history, blocker / 'session.txt')  # tried again with the next line


class PruneTests(unittest.TestCase):
    def test_sessions_older_than_seven_days_are_deleted(self):
        folder = Path(tempfile.mkdtemp())
        now = time.time()
        old = engine.history_file(now - 9 * 86400, folder)
        recent = engine.history_file(now - 2 * 86400, folder)
        other = folder / 'notes.txt'
        for path, age in ((old, 8 * 86400), (recent, 2 * 86400), (other, 30 * 86400)):
            path.write_text('[10:00:00] words\n')
            os.utime(path, (now - age, now - age))
        engine.prune_history(folder, now=now)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(other.exists(), 'only session files are ever deleted')
        self.assertEqual([p for _, p in engine.history_files(folder)], [recent])

    def test_sessions_are_listed_oldest_first(self):
        folder = Path(tempfile.mkdtemp())
        later, earlier = engine.history_file(2_000_000_000, folder), engine.history_file(1_900_000_000, folder)
        later.write_text('x\n')
        earlier.write_text('x\n')
        self.assertEqual([p for _, p in engine.history_files(folder)], [earlier, later])


class QuitTests(unittest.TestCase):
    def test_the_words_still_being_spoken_reach_the_history_at_quit(self):
        path = Path(tempfile.mkdtemp()) / 'session.txt'
        events = Events()
        worker = engine.RecognitionWorker(events, load=lambda: (StrippedRecognizer(['hello', ' there', ' friend']), FakePunct()))
        worker.set_history(path)  # sent while the models load, as the app does
        worker.start()
        events.wait('ready')
        worker.set_source(1)
        for _ in range(3):
            worker.feed(1, BLOCK.tobytes())
        events.snapshot(worker)
        self.assertFalse(path.exists(), 'nothing is final before the pause or quitting')
        worker.stop()
        worker.join(3)
        self.assertRegex(path.read_text(), r'^\[\d\d:\d\d:\d\d\] Hello there friend\.\n$')


if __name__ == '__main__':
    unittest.main(verbosity=2)
