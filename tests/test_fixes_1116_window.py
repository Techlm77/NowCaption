#!/usr/bin/env python3
"""1.1.16 caption window fixes: the floating box keeps its width, a new colour or background
keeps the rows, the chosen screen is used again when it is plugged in again, a fullscreen app
no longer floats captions once the watch stops, and a hand-edited dock width stays in range.

Like test_window.py, these construct CaptionWindow and need a GTK display (Broadway or
xvfb-run); no models or audio pipeline are started.
"""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import test_window
from test_window import FakeLayerShell, nc, shared_app

SKIP = getattr(test_window, 'SKIP', '')


@unittest.skipIf(nc is None, SKIP)
class FloatingWidthTests(unittest.TestCase):
    """Floating, the box was laid out at its natural width: the whole of an ellipsized status
    message, or a word wider than the box, so it ran off the screen past the part taking clicks."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.app.settings.set('size', 60)
        self.addCleanup(self.app.settings.set, 'size', 30)
        self.window = nc.CaptionWindow(self.app)
        self.app.window = self.window
        self.addCleanup(setattr, self.app, 'window', None)
        self.addCleanup(self.window.destroy)
        self.addCleanup(self.window.disconnect_monitors)
        if not self.window.monitor:
            self.skipTest('No monitor advertised by the display')
        self.geo = self.window.monitor.get_geometry()
        self.window.present()
        context = nc.GLib.MainContext.default()
        for _ in range(50):
            while context.pending():
                context.iteration(False)
        self.window.box_width = 300
        self.window.relayout()

    def lay_out(self):
        self.window.stage.allocate(self.geo.width, self.geo.height, -1, None)

    def test_a_long_status_message_is_cut_short_within_the_box(self):
        w = self.window
        w.show_text('hello there')
        w.show_failure('capture', 'Audio capture stopped (the device went away). Retrying in 4 s…')
        self.lay_out()
        self.assertEqual(w.box.get_width(), 300)
        self.assertTrue(w.status_label.get_layout().is_ellipsized(), 'the ellipsis never showed')
        # the box takes clicks exactly where it is drawn
        self.assertEqual(w.stage.measure(nc.Gtk.Orientation.HORIZONTAL, -1)[1], w.stage.x + 300)

    def test_a_word_wider_than_the_box_is_clipped_inside_it(self):
        w = self.window
        w.show_text('internationalization')
        self.assertGreater(w.label.measure(nc.Gtk.Orientation.HORIZONTAL, -1)[1], 300)
        self.lay_out()
        self.assertEqual(w.box.get_width(), 300)
        self.assertEqual(w.caption.get_width(), 300 - 2 * nc.PAD_X)
        self.assertEqual(w.caption.get_overflow(), nc.Gtk.Overflow.HIDDEN)
        self.assertEqual(w.box.get_height(), w.caption_height())


@unittest.skipIf(nc is None, SKIP)
class RestyleTests(unittest.TestCase):
    """A new text colour or background wrapped the passage afresh: with lines dropped from its
    start, words moved between the rows on screen, and the glide and the words arriving stopped."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.app.settings.set('size', 30)
        for key in ('opacity', 'colour', 'size'):
            self.addCleanup(self.app.settings.set, key, nc.DEFAULTS[key])
        self.window = nc.CaptionWindow(self.app)
        self.app.window = self.window
        self.addCleanup(setattr, self.app, 'window', None)
        self.addCleanup(self.window.destroy)
        self.addCleanup(self.window.disconnect_monitors)
        words = [f'word{i}' if i % 3 else f'longerword{i}' for i in range(80)]
        self.window.show_text(' '.join(words))
        w = self.window

        def rows(*passages):
            wrap = nc.Rows(w.layout, w.row_height)
            return [wrap(passage) for passage in passages][-1]
        # The passage drops its oldest words: as many as it takes, in this display's font, for
        # a fresh wrap to differ from the rows kept on screen.
        drop = next((d for d in range(1, 30) if rows(words, words[d:] + ['more']) != rows(words[d:] + ['more'])),
                    None)
        if drop is None:
            self.skipTest('In this font, every passage wraps the same afresh')
        self.words = words[drop:] + ['more']
        self.window.show_text(' '.join(self.words))

    def test_a_new_background_or_colour_keeps_the_rows(self):
        w, caption = self.window, self.window.caption
        fresh = nc.Rows(w.layout, w.row_height)(self.words)
        self.assertNotEqual(fresh, caption.lines, 'wrapped afresh, the rows would be the same anyway')
        rows, wrapper = list(caption.lines), caption.wrapper
        caption.glide, caption.appeared = 1.0, {len(self.words) - 1: nc.GLib.get_monotonic_time()}
        self.app.opacity(+0.05)
        self.app.activate_action('colour', nc.GLib.Variant('s', 'yellow'))
        self.assertIs(caption.wrapper, wrapper)
        self.assertEqual(caption.lines, rows)
        self.assertEqual(caption.glide, 1.0)
        self.assertIn(len(self.words) - 1, caption.appeared)
        self.assertEqual(w.values['background'].get_label(), f"{round(self.app.settings.get('opacity') * 100)}%")
        self.assertEqual(w.values['text-colour'].get_label(), 'Yellow')

    def test_a_new_text_size_still_fits_the_box_again(self):
        wrapper = self.window.caption.wrapper
        self.app.resize(+1)
        self.assertIsNot(self.window.caption.wrapper, wrapper)
        self.assertEqual(self.window.values['text-size'].get_label(), '36 px')

    def test_a_layout_change_with_the_same_look_still_fits(self):
        wrapper = self.window.caption.wrapper
        self.window.reset_size()
        self.assertIsNot(self.window.caption.wrapper, wrapper)


@unittest.skipIf(nc is None, SKIP)
class ScreenTests(unittest.TestCase):
    """The captions moved only when their screen went away: docked again, a laptop kept them on
    its own panel until NowCaption restarted, though the chosen screen was back."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.addCleanup(self.app.settings.set, 'monitor', nc.DEFAULTS['monitor'])
        self.window = nc.CaptionWindow(self.app)
        self.app.window = self.window
        self.addCleanup(setattr, self.app, 'window', None)
        self.addCleanup(self.window.destroy)
        self.addCleanup(self.window.disconnect_monitors)
        if not self.window.monitor:
            self.skipTest('No monitor advertised by the display')
        self.used = []
        self.window.use_monitor = self.used.append
        self.external = NS(get_connector=lambda: 'HDMI-9')

    def plugged(self, *items):
        self.window.on_monitors_changed(NS(get_n_items=lambda: len(items), get_item=items.__getitem__), 0, 0, 1)

    def test_the_chosen_screen_is_used_again_when_it_is_plugged_in(self):
        self.app.settings.set('monitor', 'HDMI-9')
        self.plugged(self.window.monitor, self.external)
        self.assertEqual(self.used, [self.external])

    def test_other_screens_plugged_in_leave_the_captions_where_they_are(self):
        self.app.settings.set('monitor', '')
        self.plugged(self.window.monitor, self.external)
        self.app.settings.set('monitor', self.window.monitor.get_connector() or '')
        self.plugged(self.window.monitor, self.external)
        self.assertEqual(self.used, [])

    def test_a_screen_unplugged_still_moves_the_captions(self):
        self.app.settings.set('monitor', 'HDMI-9')
        self.plugged(NS(get_connector=lambda: 'eDP-1'))
        self.assertEqual(len(self.used), 1)
        self.assertIsNot(self.used[0], self.external)


@unittest.skipIf(nc is None, SKIP)
class DockFixTests(unittest.TestCase):
    """Docked positions, with the FakeLayerShell of test_window.py."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.shell = FakeLayerShell()
        patcher = patch.object(nc, 'LayerShell', self.shell)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ('position', 'dock_rows', 'dock_width', 'size'):
            self.addCleanup(self.app.settings.set, key, nc.DEFAULTS[key])
        self.app.settings.set('size', 30)
        self.window = nc.CaptionWindow(self.app)
        self.app.window = self.window
        self.addCleanup(self.window.destroy)
        self.addCleanup(self.window.disconnect_monitors)
        self.addCleanup(setattr, self.app, 'window', None)
        if not self.window.monitor:
            self.skipTest('No monitor advertised by the display')
        self.geo = self.window.monitor.get_geometry()
        self.window.show_text('so we took the long road back past the old sawmill ' * 6)

    def position(self, name):
        self.app.activate_action('position', nc.GLib.Variant('s', name))

    def test_floating_while_covered_does_not_stop_docking_later(self):
        # switching to floating unloads the KWin script, which then never says the app closed
        w = self.window
        self.position('bottom')
        w.set_covered(True)
        self.assertIsNone(w.edge)
        self.position('floating')
        self.assertFalse(w.covered)
        self.position('top')
        self.assertEqual(w.edge, 'top')
        self.assertEqual((self.shell.zone, self.shell.layer), ('auto', FakeLayerShell.Layer.TOP))

    def test_a_hand_edited_dock_width_stays_in_range(self):
        w = self.window
        self.app.settings.set('dock_width', 100000)
        self.position('left')
        self.assertEqual(w.box_width, int(self.geo.width * 0.6))
        self.app.settings.set('dock_width', 10)
        w.restyle()
        self.assertEqual(w.box_width, nc.MIN_WIDTH)
        self.assertGreaterEqual(w.caption.get_size_request()[0], 0)
        self.app.settings.set('dock_width', 300)
        w.restyle()
        self.assertEqual(w.box_width, 300)
        self.app.settings.set('dock_width', -1)
        w.restyle()
        self.assertEqual(w.box_width, nc.CaptionWindow.dock_width_for(w.monitor))


if __name__ == '__main__':
    unittest.main(verbosity=2)
