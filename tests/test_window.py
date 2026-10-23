#!/usr/bin/env python3
"""Actual GTK window construction/rendering smoke tests (use xvfb-run in CI).

Unlike the application-logic tests, these construct CaptionWindow and its menu.
No models or audio pipeline are started. Missing GTK/display is a hard failure
when NOWCAPTION_REQUIRE_GUI_TESTS=1, otherwise these tests skip locally.
"""
import os
import tempfile
import time
import unittest

CONFIG = tempfile.TemporaryDirectory()
os.environ['XDG_CONFIG_HOME'] = CONFIG.name
DATA = tempfile.TemporaryDirectory()  # transcript history for these tests only
os.environ['XDG_DATA_HOME'] = DATA.name
try:
    from nowcaption import app as nc
    if not nc.Gtk.init_check() or nc.Gdk.Display.get_default() is None:
        raise RuntimeError('No GTK display available; run with xvfb-run')
except (ImportError, ValueError, RuntimeError) as e:
    if os.environ.get('NOWCAPTION_REQUIRE_GUI_TESTS') == '1':
        raise
    nc, SKIP = None, str(e)


APP = []


def shared_app():
    """One registered application for every test class: a second one could not export its
    actions on the session bus under the same path."""
    if not APP:
        app = nc.NowCaption()
        # A NowCaption that is already running owns the app ID; as its remote instance this
        # one would have no windows of its own, and GTK crashes building one.
        app.set_flags(app.get_flags() | nc.Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        APP.append(app)
    return APP[0]


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

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
        self.assertTrue({'app.smaller', 'app.larger', 'app.lighter', 'app.darker', 'app.colour'} <= actions)

    def test_text_colour_swatches_colour_the_captions_and_keep_the_menu_open(self):
        w = self.window
        self.addCleanup(self.app.activate_action, 'colour', nc.GLib.Variant('s', 'white'))
        w.present()
        w.popover.popup()
        self.settle()
        swatches = {b.get_action_target_value().get_string(): b for b in self.buttons(w.popover)
                    if b.get_action_name() == 'app.colour'}
        self.assertEqual(list(swatches), list(nc.COLOURS))
        for name, button in swatches.items():
            self.assertEqual(button.get_property('tooltip-text'), nc.COLOURS[name][0])
        swatches['yellow'].emit('clicked')
        self.settle()
        self.assertEqual(self.app.settings.get('colour'), 'yellow')
        self.assertTrue(w.popover.get_visible(), 'the menu closed after choosing a colour')
        self.assertTrue(swatches['yellow'].get_active())
        self.assertFalse(swatches['white'].get_active())
        rgba = w.label.get_color()
        self.assertEqual((rgba.red, rgba.green, rgba.blue), (1.0, 1.0, 0.0))
        swatches['yellow'].emit('clicked')  # choosing the current colour again keeps it chosen
        self.settle()
        self.assertTrue(swatches['yellow'].get_active())
        self.assertEqual(self.app.settings.get('colour'), 'yellow')
        w.popover.popdown()

    def test_a_new_window_uses_the_saved_text_colour(self):
        self.app.settings.set('colour', 'cyan')
        self.addCleanup(self.app.settings.set, 'colour', 'white')
        window = nc.CaptionWindow(self.app)
        self.addCleanup(window.destroy)
        self.addCleanup(window.disconnect_monitors)
        rgba = window.label.get_color()
        self.assertEqual((rgba.red, rgba.green, rgba.blue), (0.0, 1.0, 1.0))

    def test_the_size_and_background_rows_show_their_values(self):
        self.app.settings.set('opacity', 0.78)
        self.window.restyle()
        self.assertEqual(self.window.values['text-size'].get_label(), '30 px')
        self.assertEqual(self.window.values['background'].get_label(), '78%')
        self.app.resize(+1)
        self.assertEqual(self.window.values['text-size'].get_label(), '36 px')

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
        other = nc.CaptionWindow(self.app)
        other.present()
        self.settle()
        other.destroy()
        self.assertIsNone(other._monitors_handler)
        self.assertEqual(other._monitor_handlers, [])

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

    def test_capture_failure_stays_visible_until_explicit_recovery(self):
        self.window.show_text('Already displayed speech')
        self.window.show_failure('capture', 'Device unavailable')
        self.window.show_text('Buffered speech')
        self.assertEqual(self.window.status_label.get_text(), 'Device unavailable')
        self.window.clear_failure('capture')
        self.assertFalse(self.window.status_label.get_visible())


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class TranscriptWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.app.settings.set('history', True)
        for _, path in nc.engine.history_files():
            path.unlink()
        folder = nc.engine.history_dir()
        folder.mkdir(parents=True, exist_ok=True)
        now = time.time()
        self.older_start, self.newer_start = now - 2 * 86400, now - 3600
        self.older = nc.engine.history_file(self.older_start, folder)
        self.newer = nc.engine.history_file(self.newer_start, folder)
        self.older.write_text('[09:00:01] Good morning everyone.\n[09:00:05] Bright and early today.\n')
        self.newer.write_text('[13:00:00] Welcome back.\n')
        self.window = nc.TranscriptWindow(self.app)
        self.addCleanup(self.window.destroy)

    def text(self):
        return self.window.buffer.get_text(*self.window.buffer.get_bounds(), False)

    def test_earlier_sessions_are_shown_oldest_first_under_their_headings(self):
        text = self.text()
        self.assertLess(text.index('Good morning'), text.index('Welcome back'))
        self.assertLess(text.index(nc.session_heading(int(self.older_start))), text.index('Good morning'))
        self.assertEqual(self.window.title.get_subtitle(), 'Last 7 days')

    def test_this_session_grows_without_touching_the_earlier_text(self):
        w = self.window
        before = self.text()
        w.apply('[14:05:20] Hello there.')
        self.assertTrue(self.text().startswith(before))
        self.assertIn(nc.session_heading(self.app.started), self.text())
        w.apply('[14:05:20] Hello there, how are you?\n[14:05:26] Fine')  # the newest line changed
        self.assertTrue(self.text().endswith('[14:05:20] Hello there, how are you?\n[14:05:26] Fine\n'))
        self.assertEqual(self.text().count('Hello there'), 1)
        w.apply('')  # Clear this session
        self.assertEqual(self.text(), before)

    def test_find_counts_matches_and_steps_through_them(self):
        w = self.window
        w.apply('[14:05:20] A bright idea.')
        w.entry.set_text('bright')
        w.find()
        self.assertEqual(w.count.get_label(), '2 of 2', 'the newest match first')
        w.step(1)
        self.assertEqual(w.count.get_label(), '1 of 2')
        w.entry.set_text('nothing like this')
        w.find()
        self.assertEqual(w.count.get_label(), 'No matches')

    def test_turning_history_off_says_so(self):
        self.app.settings.set('history', False)
        self.window.show_title()
        self.assertEqual(self.window.title.get_subtitle(), 'This session only')

    def test_delete_history_removes_every_kept_session(self):
        from types import SimpleNamespace as NS
        cleared, sent = [], []
        self.app.worker = NS(clear=cleared.append, set_history=sent.append, snapshot=lambda fn, on_error=None: None)
        self.app.window = NS(show_text=lambda text: None)
        self.app.transcript = self.window
        self.app.ready = self.app.closing = self.app.quit_requested = False
        for name in ('worker', 'window', 'transcript'):
            self.addCleanup(setattr, self.app, name, None)
        self.app.delete_history()
        self.assertEqual(nc.engine.history_files(), [])
        self.assertEqual(len(cleared), 1)
        self.assertEqual(sent, [self.app.history_path])
        self.assertEqual(self.text(), '')


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        CONFIG.cleanup()
