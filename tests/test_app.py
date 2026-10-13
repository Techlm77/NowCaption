#!/usr/bin/env python3
"""Application-logic tests: the real NowCaption methods with a fake window, worker and capture.
Needs PyGObject with GTK 4 and libadwaita (no display is opened); skipped without them.

    PYTHONPATH=src .venv/bin/python tests/test_app.py
"""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace as NS

try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Gio, GLib
    os.environ['XDG_CONFIG_HOME'] = tempfile.mkdtemp()  # never touch the user's settings
    from nowcaption import app as nc
    import logging
    logging.getLogger('nowcaption').setLevel(logging.CRITICAL)
except (ImportError, ValueError) as e:
    if os.environ.get('NOWCAPTION_REQUIRE_GUI_TESTS') == '1':
        raise
    nc = None
    SKIP = f'PyGObject with GTK 4 and libadwaita is not available: {e}'


class FakeWindow:
    def __init__(self):
        self.text, self.statuses = '', []

    def show_text(self, text):
        self.text = text

    def show_status(self, message, seconds=None, sticky=False):
        self.statuses.append(message)

    def show_failure(self, scope, message):
        self.show_status(message, sticky=True)

    def clear_failure(self, scope):
        pass


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class AppTests(unittest.TestCase):
    def make(self):
        a = nc.NowCaption()
        a.window = FakeWindow()
        a.capture = NS(starts=[], start=lambda device, gen: a.capture.starts.append(gen), stop=lambda: None)
        a.worker = NS(set_source=lambda gen: None, clear=lambda gen: None, restart=lambda: None,
                      filter_calls=[])
        a.worker.set_music_filter = a.worker.filter_calls.append
        return a

    def test_quitting_during_model_loading_never_starts_capture(self):
        a = self.make()
        a.closing = True  # the user quit while the worker was still loading
        a.on_worker('ready', None)
        self.assertEqual(a.capture.starts, [])

    def test_captions_from_before_clear_cannot_repaint(self):
        a = self.make()
        a.on_worker('ready', None)
        a.on_worker('caption', (0, 'Private words'))
        a.clear_transcript()
        a.on_worker('caption', (0, 'Private words and more'))  # already queued before the clear
        self.assertEqual(a.window.text, '')
        a.on_worker('caption', (1, 'New words'))
        self.assertEqual(a.window.text, 'New words')

    def test_capture_failure_is_shown_and_retried_with_backoff(self):
        a = self.make()
        a.on_worker('ready', None)
        a.capture_ended(a.source_gen, 'device disappeared')
        self.assertIn('device disappeared', a.window.statuses[-1])
        self.assertIn('Retrying in 1 s', a.window.statuses[-1])
        a.capture_ended(a.source_gen, 'device disappeared')
        self.assertIn('Retrying in 2 s', a.window.statuses[-1])
        a.capture_ended(a.source_gen - 1, 'an old capture')  # ignored: not the current source
        self.assertEqual(len(a.window.statuses), 3)

    def test_box_fits_a_smaller_monitor(self):
        small = NS(get_geometry=lambda: NS(width=800, height=600))
        big = NS(get_geometry=lambda: NS(width=3840, height=2160))
        self.assertEqual(nc.CaptionWindow.width_for(small), 560)
        self.assertEqual(nc.CaptionWindow.width_for(big), 1000)

    def test_save_failure_is_reported(self):
        a = self.make()
        a.write_transcript(Gio.File.new_for_path('/nonexistent-folder/captions.txt'), 'hello')
        ctx, end = GLib.MainContext.default(), time.monotonic() + 5
        while not a.window.statuses and time.monotonic() < end:
            ctx.iteration(False)
            time.sleep(0.01)
        self.assertTrue(a.window.statuses and 'Could not save' in a.window.statuses[-1], a.window.statuses)

    def test_save_success_is_reported(self):
        a = self.make()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'captions.txt')
            a.write_transcript(Gio.File.new_for_path(path), 'hello')
            ctx, end = GLib.MainContext.default(), time.monotonic() + 5
            while not a.window.statuses and time.monotonic() < end:
                ctx.iteration(False)
                time.sleep(0.01)
            self.assertIn('saved', a.window.statuses[-1])
            self.assertEqual(open(path).read(), 'hello\n')



@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class MusicFilterAppTests(unittest.TestCase):
    def make(self):
        return AppTests.make(self)

    def test_music_filter_is_off_by_default_and_the_menu_toggles_and_remembers_it(self):
        a = self.make()
        action = a.lookup_action('music-filter')
        self.assertFalse(action.get_state().get_boolean())
        action.activate(None)
        self.assertTrue(action.get_state().get_boolean())
        self.assertEqual(a.settings.get('music_filter'), 1)
        self.assertEqual(a.worker.filter_calls, [True])
        action.activate(None)
        self.assertEqual(a.worker.filter_calls, [True, False])
        self.assertEqual(a.settings.get('music_filter'), 0)

    def test_a_filter_that_could_not_load_unticks_the_menu_item(self):
        a = self.make()
        action = a.lookup_action('music-filter')
        action.activate(None)
        a.on_worker('music-filter', False)
        self.assertFalse(action.get_state().get_boolean())
        self.assertEqual(a.settings.get('music_filter'), 0)


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = nc.Settings()
        self.settings.path = nc.Path(tempfile.mkdtemp()) / 'settings.ini'

    def test_hand_edited_values_are_brought_back_into_range(self):
        kf = self.settings.kf
        kf.set_integer('main', 'size', 0)
        kf.set_double('main', 'opacity', 5.0)
        kf.set_integer('main', 'rows', -3)
        kf.set_integer('main', 'width', 5)
        kf.set_string('main', 'source', 'speakers')
        self.assertEqual(self.settings.get('size'), nc.SIZES[0])
        self.assertEqual(self.settings.get('opacity'), 1.0)
        self.assertEqual(self.settings.get('rows'), 1)
        self.assertEqual(self.settings.get('width'), nc.MIN_WIDTH)
        self.assertEqual(self.settings.get('source'), 'desktop')

    def test_default_width_and_valid_values_are_kept(self):
        self.assertEqual(self.settings.get('width'), -1)
        self.settings.set('source', 'mic')
        self.settings.set('size', 42)
        self.assertEqual((self.settings.get('source'), self.settings.get('size')), ('mic', 42))

    def test_a_settings_file_that_cannot_be_written_does_not_raise(self):
        blocker = nc.Path(tempfile.mkdtemp()) / 'not-a-folder'
        blocker.write_text('')
        self.settings.path = blocker / 'settings.ini'
        self.settings.set('source', 'mic')  # must not raise
        self.assertEqual(self.settings.get('source'), 'mic')


if __name__ == '__main__':
    unittest.main(verbosity=2)
