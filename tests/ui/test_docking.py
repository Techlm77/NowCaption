#!/usr/bin/env python3
"""Floating or docked along a screen edge (with support.windows' FakeLayerShell): the strips,
resizing them, sliding between positions, and fullscreen apps. On the test display; no models
or audio pipeline are started.

DockTests come from the first window tests, DockFixTests from 1.1.16.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import style
from support.windows import SKIP, FakeLayerShell, nc, shared_app

if nc:  # GTK and a display are there
    from nowcaption import settings
    from nowcaption.desktop import plasma
    from nowcaption.ui import caption_window


@unittest.skipIf(nc is None, SKIP)
class DockTests(unittest.TestCase):
    """Floating or docked along an edge, where the compositor keeps that strip free of windows."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.shell = FakeLayerShell()
        patcher = patch.object(caption_window.layer_shell, 'LayerShell', self.shell)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ('position', 'dock_rows', 'dock_width', 'size'):
            self.addCleanup(self.app.settings.set, key, settings.DEFAULTS[key])
        self.app.settings.set('size', 30)
        self.window = caption_window.CaptionWindow(self.app)
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

    def test_docked_strips_line_up_like_panels_and_floating_stays_above_everything(self):
        self.assertEqual(self.shell.layer, FakeLayerShell.Layer.OVERLAY)
        self.position('bottom')
        self.assertEqual(self.shell.layer, FakeLayerShell.Layer.TOP)
        self.position('floating')
        self.assertEqual(self.shell.layer, FakeLayerShell.Layer.OVERLAY)

    def test_a_fullscreen_app_floats_docked_captions_until_it_closes(self):
        w = self.window
        self.position('bottom')
        w.set_covered(True)
        self.assertIsNone(w.edge)
        self.assertTrue(all(self.shell.anchors.values()))
        self.assertEqual((self.shell.zone, self.shell.layer), (-1, FakeLayerShell.Layer.OVERLAY))
        self.assertEqual(self.app.settings.get('position'), 'bottom', 'the choice in the menu stays')
        w.set_covered(False)
        self.assertEqual(w.edge, 'bottom')
        self.assertEqual((self.shell.zone, self.shell.layer), ('auto', FakeLayerShell.Layer.TOP))

    def test_the_kwin_script_watches_the_captions_screen_while_docked(self):
        watch = plasma.FullscreenWatch('io.github.Techlm77.NowCaption')
        calls = []
        watch.bus = object()
        watch.call = lambda method, args, done=None, sync=False: (calls.append((method, args)), done and done())
        watch.start('DP-2', (0, 0, 1920, 1080))
        self.assertEqual([m for m, _ in calls], ['unloadScript', 'loadScript', 'start'])
        script = Path(calls[1][1][1][0]).read_text()
        self.assertIn('var OUTPUT = "DP-2";', script)
        self.assertIn('var AREA = [0, 0, 1920, 1080];', script)  # for KWin 5, which names no screens
        self.assertIn('"io.github.Techlm77.NowCaption"', script)
        for kwin5 in ('clientList', 'activeClient', 'clientAdded', 'clientRemoved', 'clientActivated'):
            self.assertIn(kwin5, script)  # Plasma 5.27's names (Kubuntu 24.04), beside KWin 6's
        self.assertNotRegex(script, r'\b(const|let)\b|=>')  # KWin 5's script engine predates them
        calls.clear()
        watch.start('DP-2', (0, 0, 1920, 1080))  # already watching that screen
        self.assertEqual(calls, [])
        watch.start('DP-2', (0, 0, 2560, 1440))  # a new resolution: KWin 5 needs the new area
        self.assertEqual([m for m, _ in calls], ['unloadScript', 'loadScript', 'start'])
        calls.clear()
        watch.stop()
        self.assertEqual(calls, [('unloadScript', ('(s)', ('nowcaption-fullscreen',)))])

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
        self.assertEqual(w.rows, style.ROWS)
        self.assertTrue(w.has_css_class('docked'))
        w.on_layout(None, self.geo.width - 60, 98)  # a panel down one side leaves less room
        self.assertEqual(w.box_width, self.geo.width - 60)

    def test_docked_at_a_side_is_a_full_height_column(self):
        w = self.window
        self.position('left')
        self.assertEqual(self.shell.anchors, {'top': True, 'bottom': True, 'left': True, 'right': False})
        self.assertEqual(w.box_width, caption_window.CaptionWindow.dock_width_for(w.monitor))
        self.assertEqual(w.box_height(), self.geo.height)
        self.assertEqual(w.rows, (self.geo.height - 2 * style.PAD_Y) // w.row_height)
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
        self.assertEqual(w.pointer.edges_at(width // 2, 2), 'n')
        self.assertEqual(w.pointer.edges_at(width // 2, height - 2), '')
        self.assertEqual(w.pointer.edges_at(2, height // 2), '')
        # dragging the top edge one row up adds a row; the strip grows from the screen's edge
        w.pointer.resizing = ('n',)
        w.pointer.resize_dock(width // 2, -w.row_height)
        self.assertEqual(w.rows, style.ROWS + 1)
        w.pointer.on_drag_end(None, 0, 0)
        self.assertEqual(self.app.settings.get('dock_rows'), style.ROWS + 1)

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
        w.pointer.on_drag_begin(NS(set_state=lambda state: None), *press)
        self.assertEqual(w.get_cursor().get_name(), 'n-resize')
        self.assertIsNot(pressed, w)
        self.assertEqual(pressed.get_cursor().get_name(), 'n-resize')
        w.pointer.on_drag_end(None, 0, 0)
        self.assertIsNone(pressed.get_cursor())
        self.assertEqual(w.get_cursor().get_name(), 'default')

    def test_a_side_strip_resizes_by_its_inner_edge(self):
        w = self.window
        w.present()
        self.position('right')
        for _ in range(200):
            nc.GLib.MainContext.default().iteration(False)
        w.pointer.resizing = ('w',)
        w.pointer.resize_dock(-100, 10)  # 100 px left of the strip's left edge
        self.assertEqual(w.box_width, w.get_width() + 100)
        w.pointer.on_drag_end(None, 0, 0)
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
            w.menu.point_menu(x, y)
            _, rect = w.menu.popover.get_pointing_to()
            return w.menu.popover.get_position(), (rect.x, rect.y, rect.width, rect.height)

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

    # Without this, an open menu left where the captions were failed no test
    def test_an_open_menu_moves_with_the_captions_to_their_new_place(self):
        w = self.window
        w.present()
        for _ in range(50):
            nc.GLib.MainContext.default().iteration(False)
        w.menu.popover.popup()
        self.position('top')
        self.assertTrue(w.menu.popover.get_visible())
        _, moved = w.menu.popover.get_pointing_to()
        w.menu.point_menu(w.x + w.box_width // 2, w.y + w.box_height() // 2)  # where the menu belongs now
        _, expected = w.menu.popover.get_pointing_to()
        self.assertEqual((moved.x, moved.y, moved.width, moved.height),
                         (expected.x, expected.y, expected.width, expected.height))
        self.assertEqual(w.menu.popover.get_position(), nc.Gtk.PositionType.BOTTOM)  # down, away from the top edge
        w.menu.popover.popdown()

    def animated(self):
        gtk = nc.Gtk.Settings.get_default()
        gtk.set_property('gtk-enable-animations', True)
        self.addCleanup(gtk.set_property, 'gtk-enable-animations', False)
        self.window.present()
        for _ in range(200):
            nc.GLib.MainContext.default().iteration(False)

    def finish_moves(self):
        while self.window.docking.moves:
            moves, self.window.docking.moves = self.window.docking.moves, []
            for animation in moves:
                animation.skip()

    def test_moving_a_docked_strip_slides_it_into_its_edge_then_out_of_the_new_one(self):
        w = self.window
        self.position('top')
        self.animated()
        full = w.box.measure(nc.Gtk.Orientation.VERTICAL, -1)[1]  # the strip's whole height
        self.position('bottom')
        self.assertEqual(w.edge, 'top', 'moved before sliding away')
        self.assertEqual(len(w.docking.moves), 1)
        w.docking.moves[0].skip()  # slid into the top edge: now anchored at the bottom, slid away
        self.assertEqual(w.edge, 'bottom')
        self.assertEqual(self.shell.anchors, {'top': False, 'bottom': True, 'left': True, 'right': True})
        self.assertEqual(w.drawer.shown, 0)
        self.assertEqual(self.shell.zone, 0, 'reserves space before it slides in')
        # the window keeps its size all along: only the space reserved changes
        self.assertEqual(w.drawer.measure(nc.Gtk.Orientation.VERTICAL, -1)[1], full)
        self.finish_moves()
        self.assertIsNone(w.drawer.shown)
        self.assertEqual(self.shell.zone, 'auto')

    def test_only_the_part_slid_out_takes_clicks(self):
        w = self.window
        self.position('top')
        full = w.box.measure(nc.Gtk.Orientation.VERTICAL, -1)[1]  # the strip's whole height
        w.drawer.show_part('top', 40)
        self.assertEqual(w.drawer.hidden(), full - 40)
        self.assertEqual(w.docking.docked_part(1920, full), (0, 0, 1920, 40))
        self.position('bottom')
        w.drawer.show_part('bottom', 40)
        self.assertEqual(w.docking.docked_part(1920, full), (0, full - 40, 1920, 40))
        w.drawer.show_part(None, None)
        self.assertEqual(w.docking.docked_part(1920, full), (0, 0, 1920, full))

    def test_a_new_position_during_a_move_ends_there(self):
        w = self.window
        self.position('top')
        self.animated()
        self.position('bottom')
        self.position('left')
        self.finish_moves()
        self.assertEqual(w.edge, 'left')
        self.assertIsNone(w.drawer.shown)
        self.assertEqual(self.shell.anchors, {'top': True, 'bottom': True, 'left': True, 'right': False})

    def test_floating_captions_fade_out_and_dock_by_sliding_in(self):
        w = self.window
        self.animated()
        self.position('bottom')
        self.assertIsNone(w.edge)
        w.fades[w.box].skip()  # faded out where they floated
        self.assertEqual(w.edge, 'bottom')
        self.assertEqual(w.box.get_opacity(), 1.0)
        self.assertEqual(w.drawer.shown, 0)
        self.finish_moves()
        self.assertIsNone(w.drawer.shown)

    def test_a_new_text_size_keeps_a_top_strip_at_its_rows(self):
        w = self.window
        self.position('top')
        self.app.appearance.resize(+1)
        w.on_fonts()  # as GTK would once it applies the new size
        self.assertEqual(w.rows, style.ROWS)
        self.assertEqual(w.caption_height(), style.ROWS * w.row_height + 2 * style.PAD_Y)


@unittest.skipIf(nc is None, SKIP)
class DockFixTests(unittest.TestCase):
    """Docked positions, with the FakeLayerShell of support/windows.py."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.shell = FakeLayerShell()
        patcher = patch.object(caption_window.layer_shell, 'LayerShell', self.shell)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ('position', 'dock_rows', 'dock_width', 'size'):
            self.addCleanup(self.app.settings.set, key, settings.DEFAULTS[key])
        self.app.settings.set('size', 30)
        self.window = caption_window.CaptionWindow(self.app)
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
        self.assertFalse(w.docking.covered)
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
        self.assertEqual(w.box_width, style.MIN_WIDTH)
        self.assertGreaterEqual(w.caption.get_size_request()[0], 0)
        self.app.settings.set('dock_width', 300)
        w.restyle()
        self.assertEqual(w.box_width, 300)
        self.app.settings.set('dock_width', -1)
        w.restyle()
        self.assertEqual(w.box_width, caption_window.CaptionWindow.dock_width_for(w.monitor))


if __name__ == '__main__':
    unittest.main(verbosity=2)
