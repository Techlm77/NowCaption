#!/usr/bin/env python3
"""The caption window built for real on the test display: its menu, text size and colours, the
rows, screens and status messages. No models or audio pipeline are started.

WindowTests come from the first window tests; FloatingWidthTests, RestyleTests and ScreenTests
from 1.1.16.
"""
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import style
from support.windows import SKIP, FakeLayerShell, nc, shared_app

if nc:  # GTK and a display are there
    from nowcaption import settings
    from nowcaption.ui import caption_text, caption_window


@unittest.skipIf(nc is None, SKIP)
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.app.settings.set('size', 30)
        self.window = caption_window.CaptionWindow(self.app)
        self.app.window = self.window

    def tearDown(self):
        self.window.disconnect_monitors()
        self.window.destroy()
        self.app.window = None

    def test_window_and_menu_construct_with_a_status_role_on_the_caption_only(self):
        self.assertEqual(self.window.label.get_accessible_role(), nc.Gtk.AccessibleRole.STATUS)
        other = nc.Gtk.Label(label='Ordinary label')
        self.assertEqual(other.get_accessible_role(), nc.Gtk.AccessibleRole.LABEL)
        self.assertIsNotNone(self.window.menu.popover)

    def settle(self):
        context = nc.GLib.MainContext.default()
        for _ in range(50):
            while context.pending():
                context.iteration(False)

    def buttons(self, widget):
        child = widget.get_first_child()
        while child:
            if isinstance(child, nc.Gtk.Button) and child.get_action_name():
                yield child
            yield from self.buttons(child)
            child = child.get_next_sibling()

    def test_text_size_and_background_buttons_keep_the_menu_open(self):
        w = self.window
        w.present()
        w.menu.popover.popup()
        self.settle()
        found = {b.get_action_name(): b for b in self.buttons(w.menu.popover)}
        for action in ('app.smaller', 'app.larger', 'app.lighter', 'app.darker'):
            self.assertIn(action, found)
        before = self.app.settings.get('size')
        for _ in range(3):
            found['app.larger'].emit('clicked')
            self.settle()
        self.assertGreater(self.app.settings.get('size'), before)
        self.assertTrue(w.menu.popover.get_visible(), 'the menu closed after pressing a + button')
        found['app.darker'].emit('clicked')
        self.settle()
        self.assertTrue(w.menu.popover.get_visible())
        w.menu.popover.popdown()

    def test_menu_rows_survive_a_menu_rebuild(self):
        self.window.menu.update_menu(True)
        actions = {b.get_action_name() for b in self.buttons(self.window.menu.popover)}
        self.assertTrue({'app.smaller', 'app.larger', 'app.lighter', 'app.darker', 'app.colour'} <= actions)

    def test_text_colour_swatches_colour_the_captions_and_keep_the_menu_open(self):
        w = self.window
        self.addCleanup(self.app.activate_action, 'colour', nc.GLib.Variant('s', 'white'))
        w.present()
        w.menu.popover.popup()
        self.settle()
        swatches = {b.get_action_target_value().get_string(): b for b in self.buttons(w.menu.popover)
                    if b.get_action_name() == 'app.colour'}
        self.assertEqual(list(swatches), list(style.COLOURS))
        for name, button in swatches.items():
            self.assertEqual(button.get_property('tooltip-text'), style.COLOURS[name][0])
        swatches['yellow'].emit('clicked')
        self.settle()
        self.assertEqual(self.app.settings.get('colour'), 'yellow')
        self.assertTrue(w.menu.popover.get_visible(), 'the menu closed after choosing a colour')
        self.assertTrue(swatches['yellow'].get_active())
        self.assertFalse(swatches['white'].get_active())
        self.assertEqual(w.menu.values['text-colour'].get_label(), 'Yellow')
        rgba = w.label.get_color()
        self.assertEqual((rgba.red, rgba.green, rgba.blue), (1.0, 1.0, 0.0))
        swatches['yellow'].emit('clicked')  # choosing the current colour again keeps it chosen
        self.settle()
        self.assertTrue(swatches['yellow'].get_active())
        self.assertEqual(self.app.settings.get('colour'), 'yellow')
        w.menu.popover.popdown()

    def test_a_new_window_uses_the_saved_text_colour(self):
        self.app.settings.set('colour', 'cyan')
        self.addCleanup(self.app.settings.set, 'colour', 'white')
        window = caption_window.CaptionWindow(self.app)
        self.addCleanup(window.destroy)
        self.addCleanup(window.disconnect_monitors)
        rgba = window.label.get_color()
        self.assertEqual((rgba.red, rgba.green, rgba.blue), (0.0, 1.0, 1.0))

    def test_the_size_and_background_rows_show_their_values(self):
        self.app.settings.set('opacity', 0.78)
        self.window.restyle()
        self.assertEqual(self.window.menu.values['text-size'].get_label(), '30 px')
        self.assertEqual(self.window.menu.values['background'].get_label(), '78%')
        self.app.appearance.resize(+1)
        self.assertEqual(self.window.menu.values['text-size'].get_label(), '36 px')

    def test_two_row_caption_renders_and_font_size_can_change(self):
        self.window.show_text('hello ' * 100)
        self.assertLessEqual(len(self.window.label.get_text().splitlines()), style.ROWS)
        self.app.settings.set('size', 50)
        self.window.restyle()
        self.assertGreater(self.window.box_height(), 0)
        self.assertTrue(self.window.label.get_text())

    def fonts(self):
        w = self.window
        return (w.label.create_pango_layout('Ag').get_pixel_size()[1],
                w.status_label.create_pango_layout('Ag').get_pixel_size()[1] + style.PAD_Y)

    def new_size(self, step):
        """Change the text size, and wait until GTK has applied it to the labels and the window has
        fitted the box to it (on_fonts), or 5 s. The labels can measure the new font a few main
        loop iterations before GTK tells them their CSS changed (GTK 4.24 does), so the new font
        alone is not the moment to check the box; a box that never fits fails the test's check."""
        w = self.window
        before = self.fonts()
        self.app.appearance.resize(step)
        end = time.monotonic() + 5
        while (self.fonts() == before or (w.row_height, w.status_height) != self.fonts()) and time.monotonic() < end:
            nc.GLib.MainContext.default().iteration(False)
            time.sleep(0.005)
        self.assertNotEqual(self.fonts(), before, 'GTK never applied the new text size')

    def test_the_box_fits_a_new_text_size_once_gtk_applies_it(self):
        # GTK applies a new font size when it next lays the window out, after restyle() has
        # measured the old font: the box then lagged a step behind the text (1.1.10).
        w = self.window
        w.present()
        w.show_text('hello ' * 100)
        self.settle()
        for step in (+1, +1, -1):
            self.new_size(step)
            self.assertEqual((w.row_height, w.status_height), self.fonts())
            self.assertEqual(w.caption_height(), w.rows * self.fonts()[0] + 2 * style.PAD_Y)

    def placed_box(self, x, y):
        """The box placed by NowCaption itself (layer shell), at x, y, as if dragged there."""
        w = self.window
        if not w.monitor:
            self.skipTest('No monitor advertised by the display')
        for key in ('x', 'y'):
            self.addCleanup(self.app.settings.set, key, settings.DEFAULTS[key])
        w.layer = True
        w.present()
        w.show_text('hello ' * 100)
        self.settle()
        w.place(x, y)
        self.app.settings.set('x', w.x)
        self.app.settings.set('y', w.y)
        return w

    def test_a_new_text_size_keeps_a_floating_box_bottom_edge_in_place(self):
        w = self.placed_box(100, 300)
        bottom = w.y + w.box_height()
        self.new_size(+1)
        self.assertEqual(w.y + w.box_height(), bottom)
        self.assertLess(w.y, 300)
        self.new_size(-1)
        self.assertEqual(w.y + w.box_height(), bottom)
        self.assertEqual(self.app.settings.get('y'), w.y, 'the next start would put it elsewhere')

    def test_growing_upwards_at_the_top_of_the_screen_stays_on_screen(self):
        w = self.placed_box(100, 0)
        self.new_size(+1)
        self.assertEqual(w.y, 0)

    def test_rows_stay_put_when_the_passage_drops_its_oldest_line(self):
        # The passage holds the last 60 finished lines: in a long conversation each new line
        # dropped the oldest, the whole passage wrapped afresh, and words hopped between rows.
        rows = self.window.caption.wrapper
        words = [f'word{i}' if i % 3 else f'longerword{i}' for i in range(80)]
        before = rows(words)
        # rows wholly after the dropped words, but not the last row, which is still being written
        finished = [row for (start, _), row in zip(rows.spans, before) if start >= 7][:-1]
        self.assertGreater(len(finished), 2)
        after = rows(words[7:] + ['more'])
        self.assertEqual(after[-len(finished) - 1:-1], finished)

    def test_a_comma_at_the_end_of_a_row_does_not_move_words(self):
        rows = self.window.caption.wrapper
        words = [f'word{i}' for i in range(60)]
        before = rows(words)
        end = len(before[0].split()) - 1
        after = rows(words[:end] + [words[end] + ','] + words[end + 1:])
        self.assertEqual(after[0], before[0] + ',')
        self.assertEqual(after[1:], before[1:])

    def test_the_last_row_keeps_filling(self):
        rows = self.window.caption.wrapper
        self.assertEqual(rows(['The', 'music']), ['The music'])
        self.assertEqual(rows(['The', 'music', 'came']), ['The music came'])

    def test_geometry_refresh_keeps_caption_width_within_the_current_screen(self):
        if not self.window.monitor:
            self.skipTest('No monitor advertised by the display')
        self.window.on_monitor_geometry(self.window.monitor, None)
        self.assertLessEqual(self.window.box_width, self.window.monitor.get_geometry().width)
        self.assertEqual(self.window.box_width, self.window.width_for(self.window.monitor))

    def test_changing_screen_keeps_watching_screens_until_the_window_is_destroyed(self):
        # gtk4-layer-shell unrealizes and maps the window again whenever its screen changes
        # (Move to next screen, or a screen unplugged). That is not the window closing.
        w = self.window
        w.present()
        self.settle()
        w.unrealize()
        w.map()
        self.settle()
        self.assertIsNotNone(w._monitors_handler, 'screens plugged in or removed are no longer noticed')
        if w.monitor:
            self.assertTrue(w._monitor_handlers, 'resolution and scale changes are no longer noticed')
        other = caption_window.CaptionWindow(self.app)
        other.present()
        self.settle()
        other.destroy()
        self.assertIsNone(other._monitors_handler)
        self.assertEqual(other._monitor_handlers, [])

    def test_dragging_the_bottom_right_corner_adds_rows_and_width(self):
        w = self.window
        w.show_text('hello ' * 100)
        rows, width = w.rows, w.box_width
        w.pointer.resizing = ('se', w.x, w.y, w.x + w.box_width, w.y + w.caption_height())
        w.pointer.resize_by(-100, 2 * w.row_height)
        self.assertEqual(w.rows, min(rows + 2, w.max_rows()))
        self.assertEqual(w.box_width, max(style.MIN_WIDTH, width - 100))
        self.assertLessEqual(len(w.label.get_text().splitlines()), w.rows)
        w.pointer.on_drag_end(None, 0, 0)
        self.assertEqual(self.app.settings.get('rows'), w.rows)
        w.reset_size()
        self.assertEqual(w.rows, style.ROWS)
        self.assertEqual(self.app.settings.get('width'), -1)

    def test_notification_uses_a_separate_row_without_replacing_captions(self):
        self.window.show_text('Words that must remain visible')
        self.window.show_status('Transcript copied.', seconds=3)
        self.assertEqual(self.window.label.get_text(), 'Words that must remain visible')
        self.assertEqual(self.window.status_label.get_text(), 'Transcript copied.')
        self.assertTrue(self.window.status_label.get_visible())
        self.assertEqual(self.window.box_height(), self.window.caption_height() + self.window.status_height)
        self.window.show_text('New speech continues')
        self.assertEqual(self.window.label.get_text(), 'New speech continues')
        if self.window.status_timer:
            nc.GLib.source_remove(self.window.status_timer)
        self.window.end_status()
        self.assertFalse(self.window.status_label.get_visible())

    def test_capture_failure_stays_visible_until_explicit_recovery(self):
        self.window.show_text('Already displayed speech')
        self.window.show_failure('capture', 'Device unavailable')
        self.window.show_text('Buffered speech')
        self.assertEqual(self.window.status_label.get_text(), 'Device unavailable')
        self.window.clear_failure('capture')
        self.assertFalse(self.window.status_label.get_visible())


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
        self.window = caption_window.CaptionWindow(self.app)
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
        self.assertEqual(w.caption.get_width(), 300 - 2 * style.PAD_X)
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
            self.addCleanup(self.app.settings.set, key, settings.DEFAULTS[key])
        self.window = caption_window.CaptionWindow(self.app)
        self.app.window = self.window
        self.addCleanup(setattr, self.app, 'window', None)
        self.addCleanup(self.window.destroy)
        self.addCleanup(self.window.disconnect_monitors)
        words = [f'word{i}' if i % 3 else f'longerword{i}' for i in range(80)]
        self.window.show_text(' '.join(words))
        w = self.window

        def rows(*passages):
            wrap = caption_text.Rows(w.layout, w.row_height)
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
        fresh = caption_text.Rows(w.layout, w.row_height)(self.words)
        self.assertNotEqual(fresh, caption.lines, 'wrapped afresh, the rows would be the same anyway')
        rows, wrapper = list(caption.lines), caption.wrapper
        caption.glide, caption.appeared = 1.0, {len(self.words) - 1: nc.GLib.get_monotonic_time()}
        self.app.appearance.opacity(+0.05)
        self.app.activate_action('colour', nc.GLib.Variant('s', 'yellow'))
        self.assertIs(caption.wrapper, wrapper)
        self.assertEqual(caption.lines, rows)
        self.assertEqual(caption.glide, 1.0)
        self.assertIn(len(self.words) - 1, caption.appeared)
        self.assertEqual(w.menu.values['background'].get_label(), f"{round(self.app.settings.get('opacity') * 100)}%")
        self.assertEqual(w.menu.values['text-colour'].get_label(), 'Yellow')

    def test_a_new_text_size_still_fits_the_box_again(self):
        wrapper = self.window.caption.wrapper
        self.app.appearance.resize(+1)
        self.assertIsNot(self.window.caption.wrapper, wrapper)
        self.assertEqual(self.window.menu.values['text-size'].get_label(), '36 px')

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
        self.addCleanup(self.app.settings.set, 'monitor', settings.DEFAULTS['monitor'])
        self.window = caption_window.CaptionWindow(self.app)
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
class ScreenMenuTests(unittest.TestCase):
    """With layer shell, the menu offers "Move to next screen" while there is one: a screen
    plugged in or unplugged updates it. (ScreenTests above have no layer shell, so without these
    the menu not being updated failed no test.)"""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        patcher = patch.object(caption_window.layer_shell, 'LayerShell', FakeLayerShell())
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ('monitor', 'position'):
            self.addCleanup(self.app.settings.set, key, settings.DEFAULTS[key])
        self.window = caption_window.CaptionWindow(self.app)
        self.app.window = self.window
        self.addCleanup(setattr, self.app, 'window', None)
        self.addCleanup(self.window.destroy)
        self.addCleanup(self.window.disconnect_monitors)
        if not self.window.monitor:
            self.skipTest('No monitor advertised by the display')
        self.window.use_monitor = lambda monitor: None
        self.app.settings.set('monitor', '')  # no chosen screen: plugging one in moves nothing

    def plugged(self, *items):
        self.window.on_monitors_changed(NS(get_n_items=lambda: len(items), get_item=items.__getitem__), 0, 0, 1)

    def actions(self):
        menu, found = self.window.menu.popover.get_menu_model(), set()
        for i in range(menu.get_n_items()):
            section = menu.get_item_link(i, 'section')
            for j in range(section.get_n_items() if section else 0):
                action = section.get_item_attribute_value(j, 'action', None)
                if action:
                    found.add(action.get_string())
        return found

    def test_a_second_screen_plugged_in_or_unplugged_updates_the_menu(self):
        self.assertTrue(self.window.layer)
        self.plugged(self.window.monitor, NS(get_connector=lambda: 'HDMI-9'))
        self.assertIn('app.next-screen', self.actions())
        self.plugged(self.window.monitor)
        self.assertNotIn('app.next-screen', self.actions())


if __name__ == '__main__':
    unittest.main(verbosity=2)
