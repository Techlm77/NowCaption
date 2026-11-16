"""Moving and resizing the caption box with a mouse or a finger: dragging inside the box moves it,
dragging an edge or corner resizes it (in whole rows), and a finger held still opens the menu.
It changes the window's place and size through the window, and saves them when a drag ends."""
from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk

from ...style import CURSORS, EDGE, INNER, MIN_WIDTH, PAD_Y

if TYPE_CHECKING:  # for annotations only
    from .window import CaptionWindow


class Pointer:
    """The window's drag, motion and long-press gestures call these handlers. While a drag lasts,
    dragging holds where the box was grabbed, or resizing which edges move and where the box's
    edges were; held is the widget given the drag's cursor."""

    def __init__(self, window: CaptionWindow) -> None:
        self.window = window
        self.dragging = self.resizing = self.held = None
        self.move_from = None  # without layer shell: where a finger pressed, until it travels (on_drag_update)

    def cursor(self) -> str:
        """The cursor over the box away from its edges: docked captions do not move."""
        return 'default' if self.window.edge else 'grab'

    def edges_at(self, x: float, y: float) -> str:
        """Which edges of the box the pointer is on, as 'n', 'se', ...; '' inside it.
        A normal window cannot move itself, so there only the right and bottom edges resize.
        Docked, only the edge between the captions and the windows beside them resizes."""
        win = self.window
        if win.edge:
            w, h = win.get_width(), win.get_height()
            inner = INNER[win.edge]
            near = {'n': y < EDGE, 's': y >= h - EDGE, 'w': x < EDGE, 'e': x >= w - EDGE}[inner]
            return inner if near and 0 <= x < w and 0 <= y < h else ''
        bx, by = (win.x, win.y) if win.layer else (0, 0)
        x, y = x - bx, y - by
        w, h = win.box_width, win.box_height()
        if not (0 <= x < w and 0 <= y < h):
            return ''
        ns = 'n' if y < EDGE and win.layer else 's' if y >= h - EDGE else ''
        ew = 'w' if x < EDGE and win.layer else 'e' if x >= w - EDGE else ''
        return ns + ew

    def on_motion(self, controller, x, y):
        """The pointer moved: unless a drag is on, show the resize cursor for the edge under it, or the box's own."""
        if not (self.dragging or self.resizing):
            self.window.set_cursor(Gdk.Cursor.new_from_name(CURSORS.get(self.edges_at(x, y), self.cursor()), None))

    def hold_cursor(self, x: float, y: float, name: str) -> None:
        """Show cursor name until the drag ends. Once the pointer leaves the widget it pressed,
        GTK shows that widget's own cursor rather than the window's, and a docked strip moves
        under the pointer as it resizes, so the pressed widget gets the same cursor."""
        cursor = Gdk.Cursor.new_from_name(name, None)
        self.window.set_cursor(cursor)
        self.held = self.window.pick(x, y, Gtk.PickFlags.DEFAULT)
        if self.held is self.window:
            self.held = None
        if self.held:
            self.held.set_cursor(cursor)

    def on_drag_begin(self, gesture, x, y):
        """A drag starts: resize from an edge, or move the box. Docked, only the inner edge resizes;
        a finger moves a normal window only once it travels (on_drag_update)."""
        win = self.window
        edges = self.edges_at(x, y)
        if win.edge:  # docked: only the inner edge resizes, and nothing moves
            if edges:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.resizing = (edges,)
                self.hold_cursor(x, y, CURSORS[edges])
            return
        if edges:  # remember which edges, and where the box's edges were
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.resizing = (edges, win.x, win.y, win.x + win.box_width, win.y + win.caption_height())
            self.hold_cursor(x, y, CURSORS[edges])
            return
        self.hold_cursor(x, y, 'grabbing')
        if win.layer:  # x, y are overlay (monitor) coordinates; remember where the box was grabbed
            self.dragging = (x - win.x, y - win.y)
            return
        if isinstance(win.get_surface(), Gdk.Toplevel):  # the window manager moves normal windows
            if gesture.get_current_sequence() is not None:  # a finger: held still, it opens the menu instead
                self.move_from = (x, y)
                return
            self.begin_move(gesture, x, y)

    def begin_move(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        """Have the window manager move the window from (x, y), where the press is now."""
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.window.get_surface().begin_move(gesture.get_device(), gesture.get_current_button(), x, y,
                                             gesture.get_current_event_time())

    def on_drag_update(self, gesture, dx, dy):
        """The drag moved by dx, dy: resize, or move the box, or have the window manager move a
        normal window once a finger travels far enough."""
        if self.move_from:  # a finger moves the window once it travels as far as a drag must
            if math.hypot(dx, dy) >= self.window.get_settings().props.gtk_dnd_drag_threshold:
                x, y = self.move_from
                self.move_from = None
                self.begin_move(gesture, x + dx, y + dy)
            return
        if self.resizing and self.window.edge:
            ok, px, py = gesture.get_point(None)
            return ok and self.resize_dock(px, py)
        if self.resizing:
            return self.resize_by(dx, dy)
        if not self.dragging:
            return
        ok, px, py = gesture.get_point(None)
        if ok:
            self.window.place(px - self.dragging[0], py - self.dragging[1])

    def resize_by(self, dx: float, dy: float) -> None:
        """Move the dragged edges by dx, dy from where they started; the other edges stay put."""
        win = self.window
        edges, left, top, right, bottom = self.resizing
        geo = win.monitor.get_geometry() if win.monitor else None
        limit = (geo.width if win.layer else left + geo.width) if geo else left + 10000
        if 'w' in edges:
            left = max(0, min(left + dx, right - MIN_WIDTH))
        if 'e' in edges:
            right = max(left + MIN_WIDTH, min(right + dx, limit))
        rows = win.rows
        if 'n' in edges or 's' in edges:
            height = bottom - top + (dy if 's' in edges else -dy)
            rows = max(1, min(round((height - 2 * PAD_Y) / max(1, win.row_height)), win.max_rows()))
        width = int(right - left)
        if (width, rows) != (win.box_width, win.rows):
            win.box_width, win.rows = width, rows
            win.relayout()
        if win.layer:  # render() places the box, clamped to the screen
            win.x = int(left)
            win.y = int(bottom - win.caption_height() if 'n' in edges else top)
        win.render()

    def resize_dock(self, x: float, y: float) -> None:
        """Docked: the inner edge follows the pointer at x, y in the strip; the edge against the
        screen stays, and the windows beside the captions follow. Measured from that outer edge
        each time, since the strip itself moves under the pointer as it grows."""
        win = self.window
        w, h = win.get_width(), win.get_height()
        if win.edge in ('top', 'bottom'):
            height = y if win.edge == 'top' else h - y
            rows = max(1, min(round((height - 2 * PAD_Y) / max(1, win.row_height)), win.max_rows()))
            if rows != win.rows:
                win.rows = rows
                win.relayout()
        else:
            geo = win.monitor.get_geometry() if win.monitor else None
            width = max(MIN_WIDTH, min(int(x if win.edge == 'left' else w - x), int(geo.width * 0.6) if geo else 2000))
            if width != win.box_width:
                win.box_width = width
                win.relayout()
        win.render()

    def on_drag_end(self, gesture, dx, dy):
        """The drag ended: put the cursor back, and save the new size or place."""
        win, settings = self.window, self.window.settings
        self.move_from = None
        win.set_cursor(Gdk.Cursor.new_from_name(self.cursor(), None))
        if self.held:
            self.held.set_cursor(None)
            self.held = None
        if self.resizing and win.edge:
            self.resizing = None
            if win.edge in ('top', 'bottom'):
                settings.set('dock_rows', win.rows)
            else:
                settings.set('dock_width', win.box_width)
            return
        if self.resizing:
            self.resizing = None
            settings.set('width', win.box_width)
            settings.set('rows', win.rows)
            settings.set('x', win.x)
            settings.set('y', win.y)
            return
        if not self.dragging:
            return
        self.dragging = None
        settings.set('x', win.x)
        settings.set('y', win.y)
        if os.environ.get('NOWCAPTION_DEBUG_DRAG'):
            print('drag end', win.x, win.y, flush=True)

    def on_long_press(self, gesture, x, y):
        """A finger held still on the captions opens the menu there, as a right-click does. A
        finger that moves first drags them, or resizes them from an edge, and GTK cancels the
        long press; taking the press here ends a move that began with it. Without layer shell,
        the window manager moves the window only once the finger travels (on_drag_update)."""
        if self.resizing:  # an edge took the press
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.window.menu.on_menu(gesture, 1, x, y)
