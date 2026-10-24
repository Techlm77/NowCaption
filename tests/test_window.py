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
from types import SimpleNamespace as NS
from unittest.mock import patch

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
if nc:  # results show at once, except in AnimationTests
    nc.Gtk.Settings.get_default().set_property('gtk-enable-animations', False)


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
        self.assertEqual(w.values['text-colour'].get_label(), 'Yellow')
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

    def fonts(self):
        w = self.window
        return (w.label.create_pango_layout('Ag').get_pixel_size()[1],
                w.status_label.create_pango_layout('Ag').get_pixel_size()[1] + nc.PAD_Y)

    def new_size(self, step):
        """Change the text size, and wait until GTK has applied it to the labels."""
        before = self.fonts()
        self.app.resize(step)
        end = time.monotonic() + 5
        while self.fonts() == before and time.monotonic() < end:
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
            self.assertEqual(w.caption_height(), w.rows * self.fonts()[0] + 2 * nc.PAD_Y)

    def placed_box(self, x, y):
        """The box placed by NowCaption itself (layer shell), at x, y, as if dragged there."""
        w = self.window
        if not w.monitor:
            self.skipTest('No monitor advertised by the display')
        for key in ('x', 'y'):
            self.addCleanup(self.app.settings.set, key, nc.DEFAULTS[key])
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


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class AnimationTests(unittest.TestCase):
    """With the desktop's animations on, on a window that is shown. Frames come too slowly on
    a headless display to wait for them, so the caption rows are shown as of a chosen time,
    and fades are checked started, then skipped to their end."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.gtk = nc.Gtk.Settings.get_default()
        self.gtk.set_property('gtk-enable-animations', True)
        self.addCleanup(self.gtk.set_property, 'gtk-enable-animations', False)
        self.app.settings.set('size', 30)
        self.window = nc.CaptionWindow(self.app)
        self.app.window = self.window
        self.window.present()
        self.settle()
        self.finish(self.window.box)

    def tearDown(self):
        self.window.disconnect_monitors()
        self.window.destroy()
        self.app.window = None

    def settle(self):
        context = nc.GLib.MainContext.default()
        for _ in range(50):
            while context.pending():
                context.iteration(False)

    def finish(self, widget):
        """Skip a running fade to its end."""
        animation = self.window.fades.get(widget)
        if animation:
            animation.skip()

    def at(self, ms):
        """Show the caption rows as they are ms after now."""
        self.window.caption.update(nc.GLib.get_monotonic_time() + ms * 1000)

    def passage(self, rows):
        """Text that fills `rows` caption rows, and a word that starts the next row."""
        words = []
        while len(nc.wrapped_lines(self.window.layout, ' '.join(words + [f'word{len(words)}']))) <= rows:
            words.append(f'word{len(words)}')
        return ' '.join(words), f'word{len(words)}'

    def test_the_caption_box_fades_in_when_it_first_appears(self):
        window = nc.CaptionWindow(self.app)
        self.addCleanup(window.destroy)
        self.addCleanup(window.disconnect_monitors)
        window.present()
        self.settle()
        self.assertIn(window.box, window.fades)
        self.assertLess(window.box.get_opacity(), 1.0)
        window.fades[window.box].skip()
        self.assertEqual(window.box.get_opacity(), 1.0)

    def test_a_new_row_glides_up_and_its_first_word_fades_in(self):
        w = self.window
        text, word = self.passage(3)
        w.show_text(text)
        self.at(500)
        rows = w.label.get_text().splitlines()
        self.assertEqual(len(rows), w.rows)
        w.show_text(f'{text} {word}')
        # the rows stay where they were, with the new row waiting below them
        self.assertEqual(w.label.get_text().splitlines(), rows + [word])
        self.assertEqual(w.caption.offset, 0)
        spans = [(a.start_index, a.end_index) for a in w.label.get_attributes().get_attributes()]
        shown = w.label.get_text().encode()
        self.assertEqual([shown[s:e] for s, e in spans], [word.encode()])
        self.at(nc.GLIDE_MS / 2)
        self.assertGreater(w.caption.offset, 0)
        self.assertLess(w.caption.offset, w.row_height)
        self.at(nc.GLIDE_MS + 50)
        self.assertEqual(w.label.get_text().splitlines(), rows[1:] + [word])
        self.assertEqual(w.caption.offset, 0)
        self.assertIsNone(w.label.get_attributes())

    def test_without_animations_the_rows_jump_and_words_appear_at_once(self):
        self.gtk.set_property('gtk-enable-animations', False)
        w = self.window
        text, word = self.passage(3)
        w.show_text(text)
        rows = w.label.get_text().splitlines()
        w.show_text(f'{text} {word}')
        self.assertEqual(w.label.get_text().splitlines(), rows[1:] + [word])
        self.assertEqual(w.caption.offset, 0)
        self.assertIsNone(w.label.get_attributes())

    def test_corrected_punctuation_does_not_fade_words_again(self):
        w = self.window
        w.show_text('so we took the long road back')
        self.at(500)
        w.show_text('So we took the long road back,')
        self.assertIsNone(w.label.get_attributes())

    def test_the_status_row_fades_out_before_the_box_shrinks(self):
        w = self.window
        w.show_text('Words that stay')
        w.show_status('Transcript copied.')
        self.assertLess(w.status_label.get_opacity(), 1.0)
        self.finish(w.status_label)
        self.assertEqual(w.status_label.get_opacity(), 1.0)
        w.end_status()
        self.assertTrue(w.status_label.get_visible())
        self.finish(w.status_label)
        self.assertFalse(w.status_label.get_visible())
        self.assertEqual(w.box_height(), w.caption_height())

    def test_a_message_returning_while_the_row_fades_out_stays(self):
        w = self.window
        w.show_text('Words that stay')
        w.show_status('Transcript copied.')
        self.finish(w.status_label)
        w.end_status()
        w.show_status('Transcript copied.')
        self.finish(w.status_label)
        self.assertTrue(w.status_label.get_visible())
        self.assertEqual(w.status_label.get_opacity(), 1.0)

    def test_listening_breathes_until_captions_start(self):
        w = self.window
        w.show_status('Listening to desktop audio…')
        self.at(nc.BREATH_MS / 2)
        self.assertLess(w.label.get_opacity(), 0.6)
        w.show_text('first words')
        self.assertEqual(w.label.get_opacity(), 1.0)
        w.show_text('')
        w.show_failure('capture', 'Device unavailable. Restarting in 2 s…')
        self.at(nc.BREATH_MS / 2)
        self.assertEqual(w.label.get_opacity(), 1.0, 'a problem breathed like a wait')

    def test_a_new_text_colour_blends_in(self):
        end = time.monotonic() + 0.5  # CSS transitions start once the label has been drawn
        while time.monotonic() < end:
            nc.GLib.MainContext.default().iteration(False)
            time.sleep(0.005)
        self.addCleanup(self.app.activate_action, 'colour', nc.GLib.Variant('s', 'white'))
        self.app.activate_action('colour', nc.GLib.Variant('s', 'yellow'))
        self.assertGreater(self.window.label.get_color().blue, 0.0, 'the colour changed at once')

    def test_quitting_fades_the_box_out_first(self):
        self.addCleanup(setattr, self.app, 'fading_out', False)
        with patch.object(nc.Adw.Application, 'quit') as quit:
            self.app.quit()
            self.assertEqual(quit.call_count, 0)
            self.finish(self.window.box)
            self.assertEqual(self.window.box.get_opacity(), 0.0)
            self.assertEqual(quit.call_count, 1)
            self.app.quit()  # the safety timer, or a second Quit, while the box is gone
            self.assertEqual(quit.call_count, 2)


class FakeLayerShell:
    """Records what NowCaption asks of gtk4-layer-shell; the test display has none."""

    class Edge:
        TOP, BOTTOM, LEFT, RIGHT = 'top', 'bottom', 'left', 'right'

    class KeyboardMode:
        NONE, ON_DEMAND = 0, 1

    class Layer:
        OVERLAY = 3

    def __init__(self):
        self.anchors, self.zone = {}, None

    def is_supported(self):
        return True

    def set_anchor(self, window, edge, anchored):
        self.anchors[edge] = anchored

    def set_exclusive_zone(self, window, zone):
        self.zone = zone

    def auto_exclusive_zone_enable(self, window):
        self.zone = 'auto'

    def __getattr__(self, name):  # init_for_window, set_layer, set_monitor, ...
        return lambda *args: None


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class DockTests(unittest.TestCase):
    """Floating or docked along an edge, where the compositor keeps that strip free of windows."""

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

    def test_floating_covers_the_screen_and_reserves_nothing(self):
        self.assertTrue(all(self.shell.anchors.values()))
        self.assertEqual(self.shell.zone, -1)
        self.assertIsNone(self.window.edge)

    def test_docked_at_the_bottom_reserves_a_full_width_strip(self):
        w = self.window
        self.position('bottom')
        self.assertEqual(self.shell.anchors, {'top': False, 'bottom': True, 'left': True, 'right': True})
        self.assertEqual(self.shell.zone, 'auto')
        self.assertEqual((w.x, w.y), (0, 0))
        self.assertEqual(w.box_width, self.geo.width)
        self.assertEqual(w.rows, nc.ROWS)
        self.assertTrue(w.has_css_class('docked'))
        w.on_layout(None, self.geo.width - 60, 98)  # a panel down one side leaves less room
        self.assertEqual(w.box_width, self.geo.width - 60)

    def test_docked_at_a_side_is_a_full_height_column(self):
        w = self.window
        self.position('left')
        self.assertEqual(self.shell.anchors, {'top': True, 'bottom': True, 'left': True, 'right': False})
        self.assertEqual(w.box_width, nc.CaptionWindow.dock_width_for(w.monitor))
        self.assertEqual(w.box_height(), self.geo.height)
        self.assertEqual(w.rows, (self.geo.height - 2 * nc.PAD_Y) // w.row_height)
        self.position('right')
        self.assertEqual(self.shell.anchors, {'top': True, 'bottom': True, 'left': False, 'right': True})

    def test_back_to_floating_restores_the_floating_box(self):
        w = self.window
        width, rows = w.box_width, w.rows
        self.position('top')
        self.position('floating')
        self.assertTrue(all(self.shell.anchors.values()))
        self.assertEqual(self.shell.zone, -1)
        self.assertEqual((w.box_width, w.rows), (width, rows))
        self.assertFalse(w.has_css_class('docked'))

    def test_only_the_edge_facing_the_windows_resizes_a_docked_strip(self):
        w = self.window
        w.present()
        self.position('bottom')
        for _ in range(200):
            nc.GLib.MainContext.default().iteration(False)
        width, height = w.get_width(), w.get_height()
        self.assertEqual(w.edges_at(width // 2, 2), 'n')
        self.assertEqual(w.edges_at(width // 2, height - 2), '')
        self.assertEqual(w.edges_at(2, height // 2), '')
        # dragging the top edge one row up adds a row; the strip grows from the screen's edge
        w.resizing = ('n',)
        w.resize_dock(width // 2, -w.row_height)
        self.assertEqual(w.rows, nc.ROWS + 1)
        w.on_drag_end(None, 0, 0)
        self.assertEqual(self.app.settings.get('dock_rows'), nc.ROWS + 1)

    def test_the_resize_cursor_stays_while_the_strip_is_dragged(self):
        # GTK shows the pressed widget's own cursor once the pointer leaves it, and the strip
        # moves under the pointer as it grows: the cursor fell back to the plain arrow
        w = self.window
        w.present()
        self.position('bottom')
        for _ in range(200):
            nc.GLib.MainContext.default().iteration(False)
        press = (w.get_width() // 2, 2)
        pressed = w.pick(*press, nc.Gtk.PickFlags.DEFAULT)
        w.on_drag_begin(NS(set_state=lambda state: None), *press)
        self.assertEqual(w.get_cursor().get_name(), 'n-resize')
        self.assertIsNot(pressed, w)
        self.assertEqual(pressed.get_cursor().get_name(), 'n-resize')
        w.on_drag_end(None, 0, 0)
        self.assertIsNone(pressed.get_cursor())
        self.assertEqual(w.get_cursor().get_name(), 'default')

    def test_a_side_strip_resizes_by_its_inner_edge(self):
        w = self.window
        w.present()
        self.position('right')
        for _ in range(200):
            nc.GLib.MainContext.default().iteration(False)
        w.resizing = ('w',)
        w.resize_dock(-100, 10)  # 100 px left of the strip's left edge
        self.assertEqual(w.box_width, w.get_width() + 100)
        w.on_drag_end(None, 0, 0)
        self.assertEqual(self.app.settings.get('dock_width'), w.box_width)

    def test_docked_status_messages_show_in_a_corner_without_resizing_the_strip(self):
        w = self.window
        self.position('bottom')
        height = w.box_height()
        w.show_status('Transcript copied.', seconds=3)
        self.assertIs(w.status_label.get_parent(), w.rows_area)
        self.assertTrue(w.status_label.get_visible())
        self.assertEqual(w.box_height(), height)
        if w.status_timer:
            nc.GLib.source_remove(w.status_timer)
        w.end_status()
        self.position('floating')
        self.assertIs(w.status_label.get_parent(), w.box)

    def test_the_menu_opens_toward_the_room_on_screen(self):
        # KWin squeezed or cut off a menu with no room below the pointer instead of flipping it
        w, up, down = self.window, nc.Gtk.PositionType.TOP, nc.Gtk.PositionType.BOTTOM
        w.layer = True

        def menu_at(x, y):
            w.point_menu(x, y)
            ok, rect = w.popover.get_pointing_to()
            return w.popover.get_position(), (rect.x, rect.y, rect.width, rect.height)

        self.position('bottom')
        self.assertEqual(menu_at(900, 40), (up, (900, 0, 1, 1)))  # from the strip's top edge
        self.position('top')
        self.assertEqual(menu_at(900, 40), (down, (900, w.box_height() - 1, 1, 1)))
        self.position('left')
        self.assertEqual(menu_at(100, 900), (nc.Gtk.PositionType.RIGHT, (w.box_width - 1, 0, 1, w.box_height())))
        self.position('right')
        self.assertEqual(menu_at(100, 900), (nc.Gtk.PositionType.LEFT, (0, 0, 1, w.box_height())))
        self.position('floating')
        w.place(100, self.geo.height - 200)
        self.assertEqual(menu_at(w.x + 50, w.y + 10)[0], up)
        w.place(100, 50)
        self.assertEqual(menu_at(w.x + 50, w.y + 10), (down, (50, 10, 1, 1)))

    def test_a_new_text_size_keeps_a_top_strip_at_its_rows(self):
        w = self.window
        self.position('top')
        self.app.resize(+1)
        w.on_fonts()  # as GTK would once it applies the new size
        self.assertEqual(w.rows, nc.ROWS)
        self.assertEqual(w.caption_height(), nc.ROWS * w.row_height + 2 * nc.PAD_Y)


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        CONFIG.cleanup()
