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
        a.worker = NS(set_source=lambda gen: None, clear=lambda gen: None, restart=lambda: None)
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
        self.assertIn('Desktop audio', labels)
        self.assertNotIn('Background filter', labels)
        self.assertIsNone(a.lookup_action('music-filter'))


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
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
        kf.set_string('main', 'colour', 'purple')
        self.assertEqual(self.settings.get('colour'), 'white')
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

    def test_the_text_colour_is_remembered_after_a_restart(self):
        self.settings.set('colour', 'lime')
        again = nc.Settings()  # as NowCaption reads it the next time it starts
        again.path = self.settings.path
        again.kf.load_from_file(str(again.path), nc.GLib.KeyFileFlags.NONE)
        self.assertEqual(again.get('colour'), 'lime')

    def test_a_settings_file_that_cannot_be_written_does_not_raise(self):
        blocker = nc.Path(tempfile.mkdtemp()) / 'not-a-folder'
        blocker.write_text('')
        self.settings.path = blocker / 'settings.ini'
        self.settings.set('source', 'mic')  # must not raise
        self.assertEqual(self.settings.get('source'), 'mic')


if __name__ == '__main__':
    unittest.main(verbosity=2)
