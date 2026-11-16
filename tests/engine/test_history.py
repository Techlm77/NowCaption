#!/usr/bin/env python3
"""Transcript history: each session's finished lines are kept in a private file, including the
words still being spoken when NowCaption quits; sessions older than the chosen time are deleted.
The real store, Captioner and recognition worker with controlled models; no GTK, audio server or
downloaded models.

HistoryStoreTests, PruneTests and QuitTests come from 1.1.9; ClearAfterToggleTests,
WriteErrorTests and BrokenRecognitionTests from 1.1.16; HistoryDaysTests from issue #5 (how long
transcripts are kept).
"""
import errno
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support.fakes import Events, FakePunct, StrippedRecognizer

BLOCK = np.full(640, 1000, dtype=np.int16)
QUIET = np.zeros(640, dtype=np.int16)


def lines(path):
    return [line.split('] ', 1)[1] for line in path.read_text().splitlines()]


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


class ClearAfterToggleTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / 'history' / '2026-10-24 10.00.00.txt'
        self.store = engine.TranscriptStore(self.dir / 'cache')
        self.store.set_history(self.path)
        self.store.append(time.time(), 'Something private.')

    def test_clear_empties_the_history_after_it_was_turned_off(self):
        self.store.set_history(None)  # untick Keep 7 days of history
        self.store.clear()
        self.assertEqual(self.path.read_text(), '')

    def test_clear_empties_the_history_after_it_was_turned_off_and_on_again(self):
        self.store.set_history(None)
        self.store.set_history(self.path)
        self.store.clear()
        self.assertEqual(self.path.read_text(), '')
        self.store.append(time.time(), 'After the clear.')
        self.assertEqual(lines(self.path), ['After the clear.'])

    def test_clear_after_delete_history_does_not_bring_the_file_back(self):
        self.store.set_history(None)
        self.path.unlink()  # Delete history
        warnings = []
        self.store.on_warning = warnings.append
        self.store.clear()
        self.assertFalse(self.path.exists())
        self.assertEqual(warnings, [])

    def test_a_session_that_never_wrote_history_touches_no_file(self):
        other = self.dir / 'history' / '2026-10-24 11.00.00.txt'
        other.write_text('[11:00:00] Another session.\n')
        store = engine.TranscriptStore(self.dir / 'cache')
        store.set_history(other)  # nothing said yet
        store.set_history(None)
        store.clear()
        self.assertEqual(lines(other), ['Another session.'])


class WriteErrorTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.blocker = self.dir / 'history'
        self.blocker.write_text('')  # a file where the history folder should be
        self.path = self.blocker / 'session.txt'
        self.warnings = []
        self.store = engine.TranscriptStore(self.dir / 'cache', on_warning=self.warnings.append)
        self.store.set_history(self.path)

    def test_a_write_error_is_reported_once_and_the_next_line_tries_again(self):
        self.store.append(time.time(), 'Lost from the history.')
        self.store.append(time.time(), 'Also lost.')
        self.assertEqual(self.warnings, ['Could not save the history: File exists'])  # the folder's name is taken
        self.blocker.unlink()  # the problem goes away
        self.store.append(time.time(), 'Kept again.')
        self.assertEqual(lines(self.path), ['Kept again.'])
        self.assertEqual(len(self.store.lines()), 3, 'Copy and Save still have every line')
        with patch.object(engine.transcript.os, 'write', side_effect=OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))):
            self.store.append(time.time(), 'Lost again.')  # it fails again: the user is told again
        self.assertEqual(self.warnings[1:], ['Could not save the history: No space left on device'])
        self.assertEqual(lines(self.path), ['Kept again.'])

    def test_a_failed_write_leaves_no_half_line(self):
        self.blocker.unlink()
        self.store.append(time.time(), 'First.')
        real = os.write
        calls = []

        def disk_fills(fd, data):
            calls.append(len(data))
            if len(calls) == 1:
                return real(fd, data[:5])
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        with patch.object(engine.transcript.os, 'write', disk_fills):
            self.store.append(time.time(), 'Cut off by a full disk.')
        self.assertEqual(self.warnings, ['Could not save the history: No space left on device'])
        self.assertEqual(lines(self.path), ['First.'])
        self.store.append(time.time(), 'Third.')
        self.assertEqual(lines(self.path), ['First.', 'Third.'])

    def test_the_warning_reaches_the_window_through_the_worker(self):
        events = Events()
        worker = engine.RecognitionWorker(events, load=lambda: (StrippedRecognizer(['hello']), FakePunct()))
        worker.set_history(self.path)
        worker.start()
        events.wait('ready')
        worker.set_source(1)
        worker.feed(1, BLOCK.tobytes())
        events.snapshot(worker)
        worker.stop()  # the line reaches the store when NowCaption quits
        worker.join(3)
        self.assertEqual(events.wait('warning'), 'Could not save the history: File exists')


class BrokenRecognitionTests(unittest.TestCase):
    def test_quitting_after_recognition_failed_keeps_the_last_lines(self):
        path = Path(tempfile.mkdtemp()) / 'session.txt'
        events = Events()
        rec = StrippedRecognizer(['hello', ' there'])
        worker = engine.RecognitionWorker(events, load=lambda: (rec, FakePunct()))
        worker.set_history(path)
        worker.start()
        events.wait('ready')
        worker.set_source(1)
        for _ in range(2):
            worker.feed(1, BLOCK.tobytes())
        events.snapshot(worker)
        rec.fail = True
        worker.feed(1, BLOCK.tobytes())
        events.wait('error')
        self.assertTrue(worker.broken)
        worker.stop()
        worker.join(3)
        self.assertEqual(lines(path), ['Hello there.'])


class HistoryDaysTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        now = time.time()
        self.old = engine.history_file(now - 5 * 86400, self.dir)
        self.new = engine.history_file(now - 3600, self.dir)
        for path, age in ((self.old, 5 * 86400), (self.new, 3600)):
            path.write_text('[10:00:00] Hello.\n')
            os.utime(path, (now - age, now - age))

    def test_a_shorter_time_deletes_older_sessions(self):
        engine.prune_history(self.dir, days=1)
        self.assertFalse(self.old.exists())
        self.assertTrue(self.new.exists())

    def test_until_deleted_keeps_everything(self):
        engine.prune_history(self.dir, days=0)
        self.assertTrue(self.old.exists() and self.new.exists())
        self.assertEqual(engine.history_cutoff(0), float('-inf'))

    def test_the_span_reads_naturally(self):
        self.assertEqual([engine.history_span(d) for d in engine.HISTORY_CHOICES],
                         ['1 day', '7 days', '30 days', '90 days', None])


if __name__ == '__main__':
    unittest.main(verbosity=2)
