#!/usr/bin/env python3
"""The real NowCaption application with a fake window, worker and capture: what it does with the
worker's events, its menu, main(), restarting listening from More options, the history it keeps
and the signals that quit it. Needs PyGObject with GTK 4 and libadwaita (no display is opened).

AppTests, MenuTests and MainTests come from the first application tests; RestartListeningTests and
HistoryAndSignalTests from 1.1.16.
"""
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support.gtk import SKIP, nc

if nc:  # GTK is there
    from gi.repository import Gio, GLib

    from nowcaption.desktop import plasma
    from nowcaption.ui import caption_window


class FakeWindow:
    def __init__(self):
        self.text, self.statuses = '', []
        self.repositions = 0
        self.caption = NS(effects=[])
        self.caption.set_effect = lambda effect, speed: self.caption.effects.append((effect, speed))

    def reposition(self):
        self.repositions += 1

    def set_covered(self, covered):
        self.covered = covered

    def show_text(self, text):
        self.text = text

    def show_status(self, message, seconds=None, sticky=False):
        self.statuses.append(message)

    def show_failure(self, scope, message):
        self.show_status(message, sticky=True)

    def clear_failure(self, scope):
        pass


@unittest.skipIf(nc is None, SKIP)
class AppTests(unittest.TestCase):
    def make(self):
        a = nc.NowCaption()
        a.fullscreen_watch.bus = None  # never load a KWin script into the desktop running the tests
        a.panel_float.bus = None  # nor change the panels of the desktop running the tests
        a.window = FakeWindow()
        a.listening.capture = NS(starts=[], stop=lambda: None,
                                 start=lambda device, gen: a.listening.capture.starts.append(gen))
        a.listening.worker = NS(set_source=lambda gen: None, clear=lambda gen: None, restart=lambda: None)
        return a

    def test_quitting_during_model_loading_never_starts_capture(self):
        a = self.make()
        a.closing = True  # the user quit while the worker was still loading
        a.listening.on_worker('ready', None)
        self.assertEqual(a.listening.capture.starts, [])

    def test_captions_from_before_clear_cannot_repaint(self):
        a = self.make()
        a.listening.on_worker('ready', None)
        a.listening.on_worker('caption', (0, 'Private words'))
        a.listening.clear_transcript()
        a.listening.on_worker('caption', (0, 'Private words and more'))  # already queued before the clear
        self.assertEqual(a.window.text, '')
        a.listening.on_worker('caption', (1, 'New words'))
        self.assertEqual(a.window.text, 'New words')

    def test_capture_failure_is_shown_and_retried_with_backoff(self):
        a = self.make()
        a.listening.on_worker('ready', None)
        a.listening.capture_ended(a.listening.source_gen, 'device disappeared')
        self.assertIn('device disappeared', a.window.statuses[-1])
        self.assertIn('Retrying in 1 s', a.window.statuses[-1])
        a.listening.capture_ended(a.listening.source_gen, 'device disappeared')
        self.assertIn('Retrying in 2 s', a.window.statuses[-1])
        a.listening.capture_ended(a.listening.source_gen - 1, 'an old capture')  # ignored: not the current source
        self.assertEqual(len(a.window.statuses), 3)

    def test_box_fits_a_smaller_monitor(self):
        small = NS(get_geometry=lambda: NS(width=800, height=600))
        big = NS(get_geometry=lambda: NS(width=3840, height=2160))
        self.assertEqual(caption_window.CaptionWindow.width_for(small), 560)
        self.assertEqual(caption_window.CaptionWindow.width_for(big), 1000)

    def test_save_failure_is_reported(self):
        a = self.make()
        a.saving.write_transcript(Gio.File.new_for_path('/nonexistent-folder/captions.txt'), 'hello')
        ctx, end = GLib.MainContext.default(), time.monotonic() + 5
        while not a.window.statuses and time.monotonic() < end:
            ctx.iteration(False)
            time.sleep(0.01)
        self.assertTrue(a.window.statuses and 'Could not save' in a.window.statuses[-1], a.window.statuses)

    def test_save_success_is_reported(self):
        a = self.make()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'captions.txt')
            a.saving.write_transcript(Gio.File.new_for_path(path), 'hello')
            ctx, end = GLib.MainContext.default(), time.monotonic() + 5
            while not a.window.statuses and time.monotonic() < end:
                ctx.iteration(False)
                time.sleep(0.01)
            self.assertIn('saved', a.window.statuses[-1])
            self.assertEqual(Path(path).read_text(), 'hello\n')


@unittest.skipIf(nc is None, SKIP)
class MenuTests(unittest.TestCase):
    def labels(self, model):
        for i in range(model.get_n_items()):
            label = model.get_item_attribute_value(i, 'label', GLib.VariantType.new('s'))
            if label:
                yield label.get_string()
            section = model.get_item_link(i, 'section')
            if section:
                yield from self.labels(section)

    def test_the_background_filter_is_gone_from_the_menu_and_actions(self):
        a = AppTests.make(self)
        labels = list(self.labels(a.build_menu(True)))
        self.assertNotIn('Background filter', labels)
        self.assertIsNone(a.lookup_action('music-filter'))

    def test_the_menu_has_labelled_sections_and_opens_the_transcript(self):
        a = AppTests.make(self)
        labels = list(self.labels(a.build_menu(True)))
        for label in ('Appearance', 'Transcript', 'Open transcript', 'Keep 7 days of history',
                      'Clear this session', 'Reset position and size', 'Move to next screen', 'More options…', 'Quit'):
            self.assertIn(label, labels)
        self.assertNotIn('Copy transcript', labels)
        # appearance, the box's place, the transcript, then More options for the rest
        order = [labels.index(label)
                 for label in ('Appearance', 'Reset position and size', 'Transcript', 'More options…')]
        self.assertEqual(order, sorted(order))
        # what to listen to moved to More options
        self.assertNotIn('Listen to', labels)
        self.assertNotIn('Desktop audio', labels)
        self.assertIsNotNone(a.lookup_action('source'))
        self.assertNotIn('Move to next screen', list(self.labels(a.build_menu(False))))

    def test_how_new_words_arrive_is_kept_and_shown_on_the_captions(self):
        a = AppTests.make(self)
        for key, default in (('word_effect', 'fade'), ('word_speed', 'normal')):  # other tests share the file
            self.addCleanup(a.settings.set, key, default)
        self.assertEqual(a.lookup_action('word-effect').get_state().get_string(), 'fade')
        self.assertEqual(a.lookup_action('word-speed').get_state().get_string(), 'normal')
        a.lookup_action('word-effect').activate(nc.GLib.Variant('s', 'assemble'))
        a.lookup_action('word-speed').activate(nc.GLib.Variant('s', 'slow'))
        a.lookup_action('word-effect').activate(nc.GLib.Variant('s', 'sparkles'))  # not an effect
        self.assertEqual((a.settings.get('word_effect'), a.settings.get('word_speed')), ('assemble', 'slow'))
        self.assertEqual(a.window.caption.effects[-1], ('assemble', 1.75))

    def customs(self, model):
        for i in range(model.get_n_items()):
            name = model.get_item_attribute_value(i, 'custom', nc.GLib.VariantType.new('s'))
            if name:
                yield name.get_string()
            section = model.get_item_link(i, 'section')
            if section:
                yield from self.customs(section)

    def test_the_position_row_shows_where_nowcaption_places_its_box_and_floats_by_default(self):
        a = AppTests.make(self)
        self.assertIn('position', list(self.customs(a.build_menu(False, True))))
        self.assertNotIn('position', list(self.customs(a.build_menu(False, False))))
        self.assertNotIn('Grow upwards', list(self.labels(a.build_menu(False, True))))
        action = a.lookup_action('position')
        self.assertEqual(action.get_state().get_string(), 'floating')
        action.activate(nc.GLib.Variant('s', 'bottom'))
        self.assertEqual(a.settings.get('position'), 'bottom')
        self.assertEqual(a.window.repositions, 1)
        action.activate(nc.GLib.Variant('s', 'sideways'))
        self.assertEqual(a.settings.get('position'), 'bottom')
        self.assertEqual(a.window.repositions, 1)
        action.activate(nc.GLib.Variant('s', 'floating'))

    def test_kwin_saying_fullscreen_reaches_the_window(self):
        a = AppTests.make(self)
        a.on_fullscreen_call(None, ':1.5', plasma.FULLSCREEN_PATH, plasma.FULLSCREEN_IFACE, 'Changed',
                             nc.GLib.Variant('(b)', (True,)), NS(return_value=lambda value: None))
        self.assertTrue(a.window.covered)
        a.on_fullscreen(False)
        self.assertFalse(a.window.covered)

    def test_the_history_switch_is_remembered_and_reaches_the_worker(self):
        a = AppTests.make(self)
        sent = []
        a.listening.worker = NS(set_history=sent.append)
        action = a.lookup_action('history')
        self.assertTrue(action.get_state().get_boolean(), 'history is on by default')
        action.activate(None)
        self.assertFalse(a.settings.get('history'))
        action.activate(None)
        self.assertTrue(a.settings.get('history'))
        self.assertEqual(sent, [None, a.history.history_path])


@unittest.skipIf(nc is None, SKIP)
class MainTests(unittest.TestCase):
    """main() leaves without finalizing Python while a thread is still in native code: Python
    3.12 and 3.13 would end it with pthread_exit, and sherpa-onnx's C++ code then aborts."""

    def run_main(self, finished):
        from unittest.mock import patch
        exits = []

        class App:
            def run(self, argv):
                return 0

            def threads_finished(self, timeout):
                return finished

        with patch.object(nc, 'NowCaption', App), patch.object(nc.os, '_exit', exits.append):
            return nc.main(), exits

    def test_main_returns_normally_once_the_threads_have_finished(self):
        self.assertEqual(self.run_main(True), (0, []))

    def test_main_exits_at_once_while_a_thread_is_still_busy(self):
        self.assertEqual(self.run_main(False), (0, [0]))


@unittest.skipIf(nc is None, SKIP)
class RestartListeningTests(unittest.TestCase):
    def make(self):
        a = AppTests.make(self)
        a.listening.worker.restarts = 0
        a.listening.worker.restart = lambda: setattr(a.listening.worker, 'restarts', a.listening.worker.restarts + 1)
        return a

    def test_after_the_model_fails_to_load_restart_loads_it_again(self):
        a = self.make()
        source = a.settings.get('source')
        a.listening.on_worker('load-error', 'Could not load the speech model: no space left.')
        self.assertIn('To try again, open More options and press Restart.', a.window.statuses[-1])
        a.lookup_action('restart-listening').activate(None)
        self.assertEqual(a.listening.worker.restarts, 1)
        self.assertEqual(a.window.statuses[-1], 'Retrying the speech model…')
        self.assertEqual(a.settings.get('source'), source)  # the same sound source

    def test_after_recognition_gives_up_restart_listens_again_with_new_retries(self):
        a = self.make()
        a.listening.on_worker('ready', None)
        a.listening.recognition_failures = 3  # the automatic retries are spent
        a.listening.on_worker('error', (a.listening.source_gen, 0, 'Speech recognition stopped: decoder failed'))
        self.assertIn('To try again, open More options and press Restart.', a.window.statuses[-1])
        self.assertIsNone(a.listening.recognition_retry_id)
        a.lookup_action('restart-listening').activate(None)
        self.assertEqual(a.listening.capture.starts, [1, 2])  # capture started again
        self.assertEqual(a.listening.recognition_failures, 0)

    def test_restart_does_nothing_while_quitting(self):
        a = self.make()
        a.listening.on_worker('ready', None)
        a.quitting.quit_requested = True  # waiting for a save to finish
        a.lookup_action('restart-listening').activate(None)
        self.assertEqual(a.listening.capture.starts, [1])


@unittest.skipIf(nc is None, SKIP)
class HistoryAndSignalTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()  # history_dir() follows it: never the user's history
        patcher = patch.dict(os.environ, {'XDG_DATA_HOME': self.data})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = nc.NowCaption()
        self.app.fullscreen_watch.bus = None  # never load a KWin script into the desktop running the tests
        self.app.panel_float.bus = None  # nor change the panels of the desktop running the tests
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
        old, _recent = self.session(7 * 86400 + 60, 'Last week.'), self.session(2 * 86400, 'This week.')
        self.assertEqual([text for _, text in self.app.earlier_sessions()], ['[10:00:00] This week.'])
        self.assertNotIn('Last week', self.app.history.full_transcript('[12:00:00] Now.'))
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
        a.quitting.request_quit = lambda: called.append('request_quit')
        a.quitting.quit = lambda: called.append('quit')
        sources = nc.quit_on_signals(a)
        try:
            self.deliver(signal.SIGTERM, called)
            self.assertEqual(called, ['request_quit'])
            a.quitting.quit_requested = True  # still finishing a save: a second signal does not wait for it
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


@unittest.skipIf(nc is None, SKIP)
class QuitTests(unittest.TestCase):
    """app.quit(), which PyGObject's Ctrl+C fallback calls, quits the way Quit does: the caption
    box fades out first. When control/ was split from app.py, NowCaption lost its quit(), so
    Ctrl+C reached Gio.Application.quit and quit at once (found by the review of the split)."""

    def test_quit_fades_the_box_out_before_the_application_quits(self):
        a, fades, quits, timers = AppTests.make(self), [], [], []
        a.window.box = NS()
        a.window.fade = lambda widget, start, end, ms, done: fades.append((widget, end, done))
        with patch.object(nc.Adw.Application, 'quit', lambda app: quits.append(app)), \
                patch.object(GLib, 'timeout_add', lambda ms, callback: timers.append(callback) or 1):
            a.quit()
            self.assertEqual([(widget, end) for widget, end, _ in fades], [(a.window.box, 0.0)])
            self.assertEqual(quits, [])  # not before the box has faded out
            fades[0][2]()  # the fade ends
            self.assertEqual(quits, [a])


if __name__ == '__main__':
    unittest.main(verbosity=2)
