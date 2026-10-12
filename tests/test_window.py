#!/usr/bin/env python3
"""Actual GTK window construction/rendering smoke tests (use xvfb-run in CI).

Unlike the application-logic tests, these construct CaptionWindow and its menu.
No models or audio pipeline are started. Missing GTK/display is a hard failure
when NOWCAPTION_REQUIRE_GUI_TESTS=1, otherwise these tests skip locally.
"""
import os
import tempfile
import unittest

CONFIG = tempfile.TemporaryDirectory()
os.environ['XDG_CONFIG_HOME'] = CONFIG.name
try:
    from nowcaption import app as nc
    if not nc.Gtk.init_check() or nc.Gdk.Display.get_default() is None:
        raise RuntimeError('No GTK display available; run with xvfb-run')
except (ImportError, ValueError, RuntimeError) as e:
    if os.environ.get('NOWCAPTION_REQUIRE_GUI_TESTS') == '1':
        raise
    nc, SKIP = None, str(e)


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = nc.NowCaption()
        cls.app.register(None)

    @classmethod
    def tearDownClass(cls):
        cls.app.quit()

    def setUp(self):
        self.app.settings.set('size', 30)
        self.window = nc.CaptionWindow(self.app)
        self.app.window = self.window

    def tearDown(self):
        self.window.disconnect_monitors()
        self.window.destroy()
        self.app.window = None

    def test_window_and_menu_construct_with_a_status_role_on_the_caption_only(self):
        self.assertEqual(self.window.label.get_accessible_role(), nc.Gtk.AccessibleRole.STATUS)
        other = nc.Gtk.Label(label='Ordinary label')
        self.assertEqual(other.get_accessible_role(), nc.Gtk.AccessibleRole.LABEL)
        self.assertIsNotNone(self.window.popover)

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
        w.popover.popup()
        self.settle()
        found = {b.get_action_name(): b for b in self.buttons(w.popover)}
        for action in ('app.smaller', 'app.larger', 'app.lighter', 'app.darker'):
            self.assertIn(action, found)
        before = self.app.settings.get('size')
        for _ in range(3):
            found['app.larger'].emit('clicked')
            self.settle()
        self.assertGreater(self.app.settings.get('size'), before)
        self.assertTrue(w.popover.get_visible(), 'the menu closed after pressing a + button')
        found['app.darker'].emit('clicked')
        self.settle()
        self.assertTrue(w.popover.get_visible())
        w.popover.popdown()

    def test_menu_rows_survive_a_menu_rebuild(self):
        self.window.update_menu(True)
        actions = {b.get_action_name() for b in self.buttons(self.window.popover)}
        self.assertTrue({'app.smaller', 'app.larger', 'app.lighter', 'app.darker'} <= actions)

    def test_two_row_caption_renders_and_font_size_can_change(self):
        self.window.show_text('hello ' * 100)
        self.assertLessEqual(len(self.window.label.get_text().splitlines()), nc.ROWS)
        self.app.settings.set('size', 50)
        self.window.restyle()
        self.assertGreater(self.window.box_height(), 0)
        self.assertTrue(self.window.label.get_text())

    def test_geometry_refresh_keeps_caption_width_within_the_current_screen(self):
        if not self.window.monitor:
            self.skipTest('No monitor advertised by the display')
        self.window.on_monitor_geometry(self.window.monitor, None)
        self.assertLessEqual(self.window.box_width, self.window.monitor.get_geometry().width)
        self.assertEqual(self.window.box_width, self.window.width_for(self.window.monitor))

    def test_dragging_the_bottom_right_corner_adds_rows_and_width(self):
        w = self.window
        w.show_text('hello ' * 100)
        rows, width = w.rows, w.box_width
        w.resizing = ('se', w.x, w.y, w.x + w.box_width, w.y + w.caption_height())
        w.resize_by(-100, 2 * w.row_height)
        self.assertEqual(w.rows, min(rows + 2, w.max_rows()))
        self.assertEqual(w.box_width, max(nc.MIN_WIDTH, width - 100))
        self.assertLessEqual(len(w.label.get_text().splitlines()), w.rows)
        w.on_drag_end(None, 0, 0)
        self.assertEqual(self.app.settings.get('rows'), w.rows)
        w.reset_size()
        self.assertEqual(w.rows, nc.ROWS)
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


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        CONFIG.cleanup()
