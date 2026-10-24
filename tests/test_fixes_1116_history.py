#!/usr/bin/env python3
"""1.1.16 history and shutdown fixes: kill, logout and shutdown quit the way Quit does; Clear
empties the session's history after history was turned off; a history write error is reported
and the file tried again; sessions past 7 days go while NowCaption keeps running; quitting after
recognition failed still keeps the last lines.

The store and worker tests use controlled models (no audio server or downloaded models). The
application tests need PyGObject with GTK 4 and libadwaita (no display is opened).

    PYTHONPATH=src .venv/bin/python tests/test_fixes_1116_history.py
"""
import errno
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

os.environ['XDG_CACHE_HOME'] = tempfile.mkdtemp()  # never the user's folders
os.environ['XDG_CONFIG_HOME'] = tempfile.mkdtemp()
os.environ['XDG_DATA_HOME'] = tempfile.mkdtemp()

from nowcaption import engine  # noqa: E402
from test_reliability import Events, FakePunct  # noqa: E402
from test_fixes_115 import StrippedRecognizer  # noqa: E402

try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import GLib
    from nowcaption import app as nc
except (ImportError, ValueError) as e:
    if os.environ.get('NOWCAPTION_REQUIRE_GUI_TESTS') == '1':
        raise
    nc = None
    SKIP = f'PyGObject with GTK 4 and libadwaita is not available: {e}'

import logging
logging.getLogger('nowcaption').setLevel(logging.CRITICAL)  # the failures below are injected on purpose

BLOCK = np.full(640, 1000, dtype=np.int16)


def lines(path):
    return [line.split('] ', 1)[1] for line in path.read_text().splitlines()]


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
        with patch.object(engine.os, 'write', side_effect=OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))):
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
        with patch.object(engine.os, 'write', disk_fills):
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


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class AppTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()  # history_dir() follows it: never the user's history
        patcher = patch.dict(os.environ, {'XDG_DATA_HOME': self.data})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = nc.NowCaption()
        self.app.fullscreen_watch.bus = None  # never load a KWin script into the desktop running the tests
        self.folder = engine.history_dir()
        self.folder.mkdir(parents=True)

    def session(self, age, text):
        """A kept session last written `age` seconds ago."""
        now = time.time()
        path = engine.history_file(now - age - 600, self.folder)
        path.write_text(f'[10:00:00] {text}\n')
        os.utime(path, (now - age, now - age))
        return path

    def test_the_minute_timer_deletes_sessions_that_became_too_old(self):
        old, recent = self.session(8 * 86400, 'Last week.'), self.session(2 * 86400, 'This week.')
        self.assertTrue(self.app.release_memory())
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_copy_and_save_leave_out_sessions_past_seven_days_before_they_are_deleted(self):
        old, recent = self.session(7 * 86400 + 60, 'Last week.'), self.session(2 * 86400, 'This week.')
        self.assertEqual([text for _, text in self.app.earlier_sessions()], ['[10:00:00] This week.'])
        self.assertNotIn('Last week', self.app.full_transcript('[12:00:00] Now.'))
        self.assertTrue(old.exists(), 'only skipped here; prune_history deletes it')

    def deliver(self, signum, called):
        """Send the signal to this process and run the main loop until it is handled."""
        before = len(called)
        os.kill(os.getpid(), signum)
        context, end = GLib.MainContext.default(), time.monotonic() + 3
        while len(called) == before and time.monotonic() < end:
            context.iteration(False)
            time.sleep(0.01)

    def test_kill_logout_and_shutdown_quit_the_way_quit_does(self):
        a, called = self.app, []
        a.request_quit = lambda: called.append('request_quit')
        a.quit = lambda: called.append('quit')
        sources = nc.quit_on_signals(a)
        try:
            self.deliver(signal.SIGTERM, called)
            self.assertEqual(called, ['request_quit'])
            a.quit_requested = True  # still finishing a save: a second signal does not wait for it
            self.deliver(signal.SIGHUP, called)
            self.assertEqual(called, ['request_quit', 'quit'])
        finally:
            for source in sources:
                GLib.source_remove(source)

    def test_main_listens_for_the_signals_before_it_runs(self):
        order = []
        fake = NS(run=lambda argv: order.append('run') or 0, threads_finished=lambda timeout: True)
        with (patch.object(nc, 'NowCaption', lambda: fake), patch.object(nc.logging, 'basicConfig'),
              patch.object(nc, 'quit_on_signals', lambda app: order.append(('signals', app)))):
            self.assertEqual(nc.main(), 0)
        self.assertEqual(order, [('signals', fake), 'run'])

    def test_a_signal_quits_the_application(self):
        a, quits = self.app, []
        a.window = None  # nothing to fade out
        with patch.object(nc.Adw.Application, 'quit', lambda self: quits.append(self)):
            sources = nc.quit_on_signals(a)
            try:
                self.deliver(signal.SIGTERM, quits)
            finally:
                for source in sources:
                    GLib.source_remove(source)
        self.assertEqual(quits, [a])


if __name__ == '__main__':
    unittest.main(verbosity=2)
