#!/usr/bin/env python3
"""More options: choices show in the sample and reach the captions only with Apply, Restart
listens again at once, and a closed window is freed. On the test display.

OptionsWindowTests come from the first window tests, OptionsWindowFixTests from 1.1.16, and
OptionsWindowScreenTests from the distro test lab's 1366x768 laptop screen.
"""
import gc
import time
import unittest
import weakref
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import style
from support.windows import SKIP, nc, screen_size, shared_app

if nc:  # GTK and a display are there
    from nowcaption.ui import options_window


@unittest.skipIf(nc is None, SKIP)
class OptionsWindowTests(unittest.TestCase):
    def setUp(self):
        self.app = shared_app()
        self.app.open_options()
        self.window = self.app.options
        self.addCleanup(lambda: self.app.options and self.app.options.close())
        for name, default in (('word-effect', 'fade'), ('word-speed', 'normal'), ('source', 'desktop')):
            self.addCleanup(self.app.lookup_action(name).change_state, nc.GLib.Variant('s', default))
            self.addCleanup(self.app.settings.set, name.replace('-', '_'), default)

    def find(self, widget, kind):
        child = widget.get_first_child()
        while child:
            if isinstance(child, kind):
                yield child
            yield from self.find(child, kind)
            child = child.get_next_sibling()

    def checks(self):
        return {c.get_action_target_value().get_string(): c for c in self.find(self.window, nc.Gtk.CheckButton)}

    def test_choices_show_in_the_sample_and_reach_the_captions_only_with_apply(self):
        checks = self.checks()
        self.assertEqual(set(checks), set(style.WORD_EFFECTS) | {'desktop', 'mic'} |
                         {str(days) for days in nc.engine.HISTORY_CHOICES})
        self.assertFalse(self.window.apply_button.get_sensitive())
        checks['assemble'].activate()
        speeds = {b.get_label(): b for b in self.find(self.window, nc.Gtk.ToggleButton)
                  if b.get_action_name() == 'options.word-speed'}
        self.assertEqual(set(speeds), {'Fast', 'Normal', 'Slow'})
        speeds['Slow'].emit('clicked')  # activate() clicks only after the press animation
        self.assertEqual((self.window.preview.caption.effect, self.window.preview.caption.speed), ('assemble', 1.75))
        self.assertEqual((self.app.settings.get('word_effect'), self.app.settings.get('word_speed')),
                         ('fade', 'normal'))
        self.assertTrue(self.window.apply_button.get_sensitive())
        self.window.apply_button.emit('clicked')
        self.assertEqual((self.app.settings.get('word_effect'), self.app.settings.get('word_speed')),
                         ('assemble', 'slow'))
        self.assertEqual(self.app.lookup_action('word-effect').get_state().get_string(), 'assemble')
        self.assertFalse(self.window.apply_button.get_sensitive())

    def test_closing_without_apply_drops_the_choices(self):
        self.checks()['rain'].activate()
        self.checks()['mic'].activate()
        self.window.close()
        self.assertIsNone(self.app.options)
        self.assertEqual((self.app.settings.get('word_effect'), self.app.settings.get('source')), ('fade', 'desktop'))
        self.app.open_options()  # opens again with what the captions use
        self.window = self.app.options
        self.assertTrue(self.checks()['fade'].get_active())
        self.assertTrue(self.checks()['desktop'].get_active())

    def test_listen_to_lives_here_and_waits_for_apply_too(self):
        checks = self.checks()
        self.assertTrue(checks['desktop'].get_active())
        checks['mic'].activate()
        self.assertEqual(self.app.settings.get('source'), 'desktop')
        self.window.apply_button.emit('clicked')
        self.assertEqual(self.app.settings.get('source'), 'mic')

    def test_speed_is_greyed_out_for_words_that_appear_at_once(self):
        [speed] = [row for row in self.find(self.window, nc.Adw.ActionRow) if row.get_title() == 'Speed']
        self.assertTrue(speed.get_sensitive())
        self.checks()['none'].activate()
        self.assertFalse(speed.get_sensitive())
        self.checks()['rain'].activate()
        self.assertTrue(speed.get_sensitive())

    def test_the_sample_keeps_adding_words_and_starts_again(self):
        preview = self.window.preview
        preview.play(False)
        preview.shown = 0
        preview.next_word()
        preview.next_word()
        self.assertEqual(preview.caption.words, options_window.Preview.SAMPLE[:2])
        preview.shown = len(options_window.Preview.SAMPLE)
        preview.next_word()
        self.assertEqual(preview.caption.words, options_window.Preview.SAMPLE[:1])

    def test_closing_forgets_the_window(self):
        self.window.close()
        self.assertIsNone(self.app.options)


@unittest.skipIf(nc is None, SKIP)
class OptionsWindowFixTests(unittest.TestCase):
    def setUp(self):
        self.app = shared_app()
        self.app.open_options()
        self.window = self.app.options
        self.addCleanup(lambda: self.app.options and self.app.options.close())
        for name, default in (('word-effect', 'fade'), ('word-speed', 'normal'), ('source', 'desktop')):
            self.addCleanup(self.app.lookup_action(name).change_state, nc.GLib.Variant('s', default))
            self.addCleanup(self.app.settings.set, name.replace('-', '_'), default)

    def find(self, widget, kind):
        child = widget.get_first_child()
        while child:
            if isinstance(child, kind):
                yield child
            yield from self.find(child, kind)
            child = child.get_next_sibling()

    def checks(self):
        return {c.get_action_target_value().get_string(): c for c in self.find(self.window, nc.Gtk.CheckButton)}

    def settle(self):
        context = nc.GLib.MainContext.default()
        for _ in range(50):
            while context.pending():
                context.iteration(False)

    def test_restart_sits_under_listen_to_and_listens_again_at_once_without_apply(self):
        [row] = [row for row in self.find(self.window, nc.Adw.ActionRow) if row.get_title() == 'Start again']
        self.assertEqual(row.get_ancestor(nc.Adw.PreferencesGroup).get_title(), 'Listen to')
        [restart] = [b for b in self.find(row, nc.Gtk.Button) if b.get_label() == 'Restart']
        self.checks()['mic'].activate()  # a choice that waits for Apply
        with patch.object(self.app.listening, 'ready', True), \
                patch.object(self.app.listening, 'start_capture') as start:
            restart.emit('clicked')
        start.assert_called_once_with()
        self.assertEqual(self.app.settings.get('source'), 'desktop')  # what the captions use, not the waiting choice
        self.assertTrue(self.window.apply_button.get_sensitive())  # the microphone still waits for Apply
        self.assertTrue(self.checks()['mic'].get_active())

    def test_a_closed_window_is_freed(self):
        self.settle()  # shown, so the sample is playing
        self.assertTrue(self.window.preview.timer)
        self.checks()['rain'].activate()  # the handlers GTK holds have all run
        self.window.apply_button.emit('clicked')
        self.window.preview.row_height = None
        self.window.preview.label.styled()  # the sample still fits itself when GTK restyles it
        self.assertTrue(self.window.preview.row_height)
        window, preview = weakref.ref(self.window), weakref.ref(self.window.preview)
        self.window.close()
        del self.window
        self.settle()
        gc.collect()
        self.assertIsNone(window())
        self.assertIsNone(preview())


@unittest.skipIf(nc is None, SKIP)
class OptionsWindowScreenTests(unittest.TestCase):
    """On Debian 13 GNOME at 1366x768 the distro test lab found More options taller than the
    screen: its bottom options ran off the screen, out of reach. It must open within the space
    a laptop's desktop leaves it, with Apply in view and every option reachable by scrolling,
    and as before on a bigger screen."""

    TOP_BAR = 32  # GNOME's top bar, which the lab's desktop has above the windows

    def setUp(self):
        self.app = shared_app()

    def find(self, widget, kind):
        child = widget.get_first_child()
        while child:
            if isinstance(child, kind):
                yield child
            yield from self.find(child, kind)
            child = child.get_next_sibling()

    def settle(self, seconds=0.3):
        """Run the main loop for a while: the window is laid out on the frame clock's next tick."""
        context, end = nc.GLib.MainContext.default(), time.monotonic() + seconds
        while True:
            while context.pending():
                context.iteration(False)
            if time.monotonic() >= end:
                return
            time.sleep(0.01)

    def open_on(self, width, height):
        """More options shown on a width x height screen, and the height it asked for. GTK fits
        a window to the test display's own screen as it shows it, so what the window asks the
        desktop for is read before that."""
        with screen_size(width, height):
            window = options_window.OptionsWindow(self.app)
            self.addCleanup(window.close)
            window.realize()
            asked = window.get_default_size().height
            window.present()
            self.settle()
        return window, asked

    def test_it_fits_a_laptop_screen_with_every_option_in_reach(self):
        for width, height in ((1366, 768), (1280, 720)):
            with self.subTest(screen=f'{width}x{height}'):
                window, asked = self.open_on(width, height)
                room = height - self.TOP_BAR
                self.assertLessEqual(asked, room)
                self.assertLessEqual(window.get_height(), room)
                # Apply is in the header bar, outside what scrolls, so it is always in view
                self.assertIsNone(window.apply_button.get_ancestor(nc.Gtk.ScrolledWindow))
                self.assertTrue(window.apply_button.get_mapped())
                [page] = self.find(window, nc.Adw.PreferencesPage)
                scroller = next(self.find(page, nc.Gtk.ScrolledWindow))
                ok, view = scroller.compute_bounds(window)
                self.assertTrue(ok)
                self.assertLessEqual(view.get_y() + view.get_height(), window.get_height())
                last = [row for row in self.find(page, nc.Adw.PreferencesRow) if row.is_visible()][-1]
                adjustment = scroller.get_vadjustment()
                self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())  # it does scroll here
                adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
                self.settle()
                ok, bounds = last.compute_bounds(scroller)
                self.assertTrue(ok)
                self.assertGreaterEqual(bounds.get_y(), 0)
                self.assertLessEqual(bounds.get_y() + bounds.get_height(), scroller.get_height())

    def test_on_a_bigger_screen_it_opens_as_before(self):
        _, asked = self.open_on(1920, 1080)
        self.assertEqual(asked, 760)


if __name__ == '__main__':
    unittest.main(verbosity=2)
