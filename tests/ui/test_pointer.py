#!/usr/bin/env python3
"""Reaching the menu without a mouse or keyboard focus (1.1.16): a long press on the captions
opens the menu where a right-click would, apart from dragging and resizing. On the test
display; no models or audio pipeline are started.
"""
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from support.windows import SKIP, FakeLayerShell, nc, shared_app

if nc:  # GTK and a display are there
    from nowcaption import settings
    from nowcaption.ui import caption_window


def press():
    """A stand-in for the gesture of one press, recording the states it is given."""
    gesture = NS(states=[])
    gesture.set_state = gesture.states.append
    return gesture


@unittest.skipIf(nc is None, SKIP)
class LongPressTests(unittest.TestCase):
    """On layer shell, where the captions never have keyboard focus."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.shell = FakeLayerShell()
        self.shell.modes = []
        self.shell.set_keyboard_mode = lambda window, mode: self.shell.modes.append(mode)
        patcher = patch.object(caption_window.layer_shell, 'LayerShell', self.shell)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ('position', 'x', 'y', 'width', 'rows', 'size'):
            self.addCleanup(self.app.settings.set, key, settings.DEFAULTS[key])
        self.app.settings.set('size', 30)
        self.window = caption_window.CaptionWindow(self.app)
        self.app.window = self.window
        self.addCleanup(self.window.destroy)
        self.addCleanup(self.window.disconnect_monitors)
        self.addCleanup(setattr, self.app, 'window', None)
        if not self.window.monitor:
            self.skipTest('No monitor advertised by the display')
        self.window.show_text('so we took the long road back past the old sawmill')
        self.window.present()
        self.settle()
        self.shell.modes = []  # from here on; the window starts without keyboard input

    def settle(self, seconds=0.0):
        context, end = nc.GLib.MainContext.default(), time.monotonic() + seconds
        while True:
            while context.pending():
                context.iteration(False)
            if time.monotonic() >= end:
                return
            time.sleep(0.01)

    def menu(self):
        ok, rect = self.window.menu.popover.get_pointing_to()
        return self.window.menu.popover.get_position(), (rect.x, rect.y, rect.width, rect.height)

    def test_the_long_press_is_for_touch_and_apart_from_the_drag(self):
        controllers = self.window.observe_controllers()
        controllers = [controllers.get_item(i) for i in range(controllers.get_n_items())]
        holds = [c for c in controllers if isinstance(c, nc.Gtk.GestureLongPress)]
        drag = next(c for c in controllers if isinstance(c, nc.Gtk.GestureDrag))
        self.assertEqual(len(holds), 1)
        # a mouse has its right button, and pressing it still before dragging stays a drag
        self.assertTrue(holds[0].get_touch_only())
        # in a group of its own: when it claims the press, GTK denies the press to the drag and
        # ends it; when the drag claims it first (an edge), GTK cancels the long press
        self.assertNotIn(drag, holds[0].get_group())

    def test_holding_a_finger_on_the_captions_opens_the_menu_where_a_right_click_would(self):
        w = self.window
        w.place(100, 50)
        x, y = w.x + 50, w.y + 20
        w.menu.point_menu(x, y)
        right_click = self.menu()
        w.menu.point_menu(w.x + 30, w.y + 30)  # elsewhere, so the long press must point it again
        w.pointer.on_drag_begin(press(), x, y)  # the finger going down starts a move
        self.assertTrue(w.pointer.dragging)
        hold = press()
        w.pointer.on_long_press(hold, x, y)
        self.assertEqual(hold.states, [nc.Gtk.EventSequenceState.CLAIMED])
        self.assertEqual(self.menu(), right_click)
        self.assertEqual(self.shell.modes, [FakeLayerShell.KeyboardMode.ON_DEMAND])  # its items take keys
        self.settle(0.3)
        self.assertTrue(w.menu.popover.get_visible())
        w.menu.popover.popdown()
        self.assertEqual(self.shell.modes[-1], FakeLayerShell.KeyboardMode.NONE)
        w.pointer.on_drag_end(None, 0, 0)  # as GTK ends the denied drag
        self.assertEqual((self.app.settings.get('x'), self.app.settings.get('y')), (100, 50))

    def test_a_long_press_on_a_docked_strip_opens_the_menu_away_from_its_edge(self):
        w = self.window
        self.app.activate_action('position', nc.GLib.Variant('s', 'bottom'))
        self.settle()
        x, y = w.get_width() // 2, w.get_height() - 5  # inside, away from the edge that resizes
        w.pointer.on_drag_begin(press(), x, y)
        self.assertFalse(w.pointer.resizing)
        hold = press()
        w.pointer.on_long_press(hold, x, y)
        self.assertEqual(hold.states, [nc.Gtk.EventSequenceState.CLAIMED])
        self.assertEqual(self.menu(), (nc.Gtk.PositionType.TOP, (x, 0, 1, 1)))
        self.settle(0.3)
        w.menu.popover.popdown()

    def test_holding_still_on_an_edge_keeps_resizing_and_opens_no_menu(self):
        w = self.window
        w.place(100, 50)
        x, y = w.x + 50, w.y + 2  # on the top edge
        edge = press()
        w.pointer.on_drag_begin(edge, x, y)
        self.assertEqual(edge.states, [nc.Gtk.EventSequenceState.CLAIMED])
        self.assertEqual(w.pointer.resizing[0], 'n')
        w.menu.point_menu(w.x + 30, w.y + 30)
        before = self.menu()
        hold = press()
        w.pointer.on_long_press(hold, x, y)
        self.assertEqual(hold.states, [])
        self.assertEqual(self.menu(), before)
        self.assertEqual(self.shell.modes, [])
        self.assertEqual(w.pointer.resizing[0], 'n')
        w.pointer.on_drag_end(None, 0, 0)


    # The tests here call the Pointer's handlers; this one checks the window's gesture reaches
    # them (without it, a window that stopped connecting drag-begin failed no test).
    def test_dragging_a_corner_of_the_box_resizes_it_through_the_windows_drag_gesture(self):
        w = self.window
        controllers = w.observe_controllers()
        [drag] = [c for c in (controllers.get_item(i) for i in range(controllers.get_n_items()))
                  if isinstance(c, nc.Gtk.GestureDrag)]
        drag.emit('drag-begin', w.x + w.box_width - 1, w.y + w.box_height() - 1)
        self.assertIsNotNone(w.pointer.resizing, 'the drag gesture did not reach the pointer')
        self.assertEqual(w.pointer.resizing[0], 'se')
        drag.emit('drag-end', 0, 0)
        self.assertIsNone(w.pointer.resizing)


@unittest.skipIf(nc is None, SKIP)
class LongPressWithoutLayerShellTests(unittest.TestCase):
    """Without layer shell (GNOME), where the window manager moves the captions' window: a
    finger moves it only once it travels, so holding it still can open the menu."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.window = caption_window.CaptionWindow(self.app)
        self.app.window = self.window
        self.addCleanup(self.window.destroy)
        self.addCleanup(self.window.disconnect_monitors)
        self.addCleanup(setattr, self.app, 'window', None)
        if self.window.layer:
            self.skipTest('This display has layer shell')
        self.moves = []
        self.window.pointer.begin_move = lambda gesture, x, y: self.moves.append((round(x), round(y)))
        self.window.present()

    def finger(self, touch=True):
        gesture = press()
        gesture.get_current_sequence = lambda: object() if touch else None
        return gesture

    def test_a_finger_held_still_opens_the_menu_and_never_moves_the_window(self):
        w, gesture = self.window, self.finger()
        w.pointer.on_drag_begin(gesture, 100, 30)
        w.pointer.on_drag_update(gesture, 2, 1)  # a finger never holds perfectly still
        self.assertEqual(self.moves, [])
        with patch.object(w.menu, 'on_menu') as menu:
            w.pointer.on_long_press(press(), 100, 30)
        menu.assert_called_once()
        w.pointer.on_drag_end(gesture, 2, 1)  # GTK ends the drag once the long press takes the press
        self.assertIsNone(w.pointer.move_from)
        self.assertEqual(self.moves, [])

    def test_a_finger_that_travels_moves_the_window_from_where_it_is(self):
        w, gesture = self.window, self.finger()
        w.pointer.on_drag_begin(gesture, 100, 30)
        far = w.get_settings().props.gtk_dnd_drag_threshold
        w.pointer.on_drag_update(gesture, far, 0)
        self.assertEqual(self.moves, [(100 + far, 30)])
        w.pointer.on_drag_update(gesture, far + 5, 0)  # the window manager has it now
        self.assertEqual(len(self.moves), 1)

    def test_the_mouse_still_moves_the_window_at_once(self):
        self.window.pointer.on_drag_begin(self.finger(touch=False), 100, 30)
        self.assertEqual(self.moves, [(100, 30)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
