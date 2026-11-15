"""The caption box's window, floating where it is dragged or docked along an edge of the screen
(layer shell), with its menu and resizing, and the Stage and Drawer that place and slide the box."""
import math
import os

import cairo
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

try:
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None

from .caption_text import CaptionText, StyledLabel  # noqa: E402
from .options_window import Preview  # noqa: E402
from .style import (ALONG, BOTTOM_GAP, CHANGE_MS, COLOURS, CURSORS, DOCK_MS, EDGE, FADE_IN_MS,  # noqa: E402
                    FADE_OUT_MS, INNER, MENU_INDENT, MIN_WIDTH, PAD_X, PAD_Y, POSITIONS, ROWS, STATUS_MS,
                    STEPPERS, UNDOCK_MS, css_font)


class Stage(Gtk.Widget):
    """The window's content. Floating, it holds the caption box at x, y at the box's own size,
    inside an overlay covering the screen. Docked, the box fills the window, whose size along
    the docked edge the compositor sets from the room beside any panels: the window must not
    ask for more, or it would cover them.

    Floating, the box's width is the width it is set to (its minimum): its natural width can
    be far wider, the whole of an ellipsized status message or a word wider than the box, and
    the box then ran off the screen, past the part that takes clicks, with no ellipsis shown."""

    def __init__(self, child):
        super().__init__()
        self.child, self.x, self.y, self.fill = child, 0, 0, False
        child.set_parent(self)

    def do_dispose(self):
        if self.child.get_parent() is self:
            self.child.unparent()
        Gtk.Widget.do_dispose(self)

    def move(self, x, y):
        self.x, self.y = x, y
        self.queue_allocate()

    def do_measure(self, orientation, for_size):
        minimum, natural, _, _ = self.child.measure(orientation, -1)
        if orientation == Gtk.Orientation.HORIZONTAL and not self.fill:
            natural = minimum  # the box's set width (a normal window takes its natural size)
        at = 0 if self.fill else (self.x if orientation == Gtk.Orientation.HORIZONTAL else self.y)
        return minimum + at, natural + at, -1, -1

    def do_size_allocate(self, width, height, baseline):
        w_min = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        if self.fill:
            w = max(width, w_min)
            self.child.allocate(w, max(height, self.child.measure(Gtk.Orientation.VERTICAL, w)[0]), -1, None)
            return
        h_nat = self.child.measure(Gtk.Orientation.VERTICAL, w_min)[1]
        at = Gsk.Transform().translate(Graphene.Point().init(self.x, self.y))
        self.child.allocate(w_min, h_nat, -1, at)


class Drawer(Gtk.Widget):
    """Holds the caption box and can show only part of it, from the edge it is docked to:
    `shown` px of its height (top or bottom) or width (left or right), the rest slid away into
    that edge. The drawer keeps the box's whole size, so the window does not resize while the
    captions slide (each resize waits for the compositor, which halved the frame rate);
    CaptionWindow shrinks and grows only the space it reserves. shown None: all of it."""

    def __init__(self, child):
        super().__init__(overflow=Gtk.Overflow.HIDDEN)
        self.child, self.edge, self.shown = child, None, None
        child.set_parent(self)

    def do_dispose(self):
        if self.child.get_parent() is self:
            self.child.unparent()
        Gtk.Widget.do_dispose(self)

    def axis(self):
        return Gtk.Orientation.VERTICAL if self.edge in ('top', 'bottom') else Gtk.Orientation.HORIZONTAL

    def show_part(self, edge, shown):
        self.edge, self.shown = edge, None if shown is None else max(0.0, shown)
        self.queue_allocate()

    def full(self):
        """The box's whole size across the docked edge."""
        return self.child.measure(self.axis(), -1)[1]

    def hidden(self):
        """How far the box is slid away into its edge, in px."""
        return 0 if self.shown is None or not self.edge else max(0, self.full() - self.shown)

    def do_measure(self, orientation, for_size):
        minimum, natural, _, _ = self.child.measure(orientation, for_size)
        return minimum, natural, -1, -1

    def do_size_allocate(self, width, height, baseline):
        w = max(width, self.child.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        h = max(height, self.child.measure(Gtk.Orientation.VERTICAL, w)[0])
        away = self.hidden()
        move = None
        if away:
            dx, dy = {'top': (0, -away), 'bottom': (0, away), 'left': (-away, 0), 'right': (away, 0)}[self.edge]
            move = Gsk.Transform().translate(Graphene.Point().init(dx, dy))
        self.child.allocate(w, h, -1, move)


class CaptionWindow(Gtk.ApplicationWindow):
    """Caption box of a chosen width and number of rows, newest words at the end of the last row.
    Dragging inside the box moves it; dragging its edges or corners resizes it, snapping the
    height to whole rows.

    On compositors with layer-shell (KDE Plasma, Hyprland, Sway, ...) the window is a transparent
    overlay covering one monitor. Only the caption box takes pointer input (everything else clicks
    through), and dragging moves the box inside the overlay, so nothing moves under the pointer.
    Elsewhere it is an undecorated normal window that the window manager moves.
    """

    def __init__(self, app):
        super().__init__(application=app, title='NowCaption')
        self.app, self.settings = app, app.settings
        self.set_decorated(False)
        self.set_resizable(False)
        self.layer = bool(LayerShell and LayerShell.is_supported())
        self.dragging = self.resizing = self.held = None
        self.move_from = None  # without layer shell: where a finger pressed, until it travels (on_drag_update)
        self.rows = self.settings.get('rows')
        self.text, self.status, self.status_timer = '', '', None
        self.status_sticky = False
        self.failures, self.status_scope = {}, None
        self.monitor = self.pick_monitor(self.settings.get('monitor'))
        self.box_width = self.width_for(self.monitor)
        self.edge = self.span = self.fitted = None  # see dock(), on_layout() and fit()
        self.covered = False  # a fullscreen app on this screen: docked captions float over it

        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.add_css_class('nowcaption')
        self.label = StyledLabel(self.on_fonts, xalign=0, yalign=0, wrap=False, single_line_mode=False,
                                 accessible_role=Gtk.AccessibleRole.STATUS)
        self.label.add_css_class('caption-text')
        self.caption = CaptionText(self.label, margin_start=PAD_X, margin_end=PAD_X,
                                   margin_top=PAD_Y, margin_bottom=PAD_Y)
        self.caption.set_effect(*app.word_effect())
        self.rows_area = Gtk.Overlay(child=self.caption)  # docked, status messages show over it
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.add_css_class('caption-box')
        self.box.append(self.rows_area)
        self.status_label = StyledLabel(self.on_fonts, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                        single_line_mode=True, accessible_role=Gtk.AccessibleRole.STATUS)
        self.status_label.add_css_class('caption-status')
        self.status_label.set_visible(False)
        self.box.append(self.status_label)
        self.footer_shown = self.footer_hiding = False
        self.fades, self.faded_in = {}, False
        self.box.connect('map', self.fade_in)
        self.drawer = Drawer(self.box)  # docked, slides the captions into their edge and out
        self.moves, self.moving = [], 0
        self.stage = Stage(self.drawer)  # floating, the box moves inside the window while dragged
        self.set_child(self.stage)

        if self.layer:
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_namespace(self, 'nowcaption')
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            if self.monitor:
                LayerShell.set_monitor(self, self.monitor)
            self.set_resizable(True)  # or the compositor cannot stretch it across the edges it is anchored to
            self.dock()
            self.connect('realize', lambda *a: self.get_surface().connect('layout', self.on_layout))
        self.restyle()

        drag = Gtk.GestureDrag()  # primary button
        drag.connect('drag-begin', self.on_drag_begin)
        drag.connect('drag-update', self.on_drag_update)
        drag.connect('drag-end', self.on_drag_end)
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()  # show a resize cursor over the edges
        motion.connect('motion', self.on_motion)
        self.add_controller(motion)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect('pressed', self.on_menu)
        self.add_controller(click)
        hold = Gtk.GestureLongPress(touch_only=True)  # a touchscreen's right-click, see on_long_press
        hold.connect('pressed', self.on_long_press)
        self.add_controller(hold)
        self.set_cursor(Gdk.Cursor.new_from_name(self.cursor(), None))

        self.popover = Gtk.PopoverMenu()
        self.update_menu(self.layer and self.monitor_count() > 1)
        self._monitor_handlers, self._monitors_handler = [], None
        self._monitors = Gdk.Display.get_default().get_monitors()
        self.watch_screens()
        # gtk4-layer-shell unrealizes and maps the window again whenever its screen changes,
        # so watch again on realize; destroying the window unrealizes it for good.
        self.connect('realize', self.watch_screens)
        self.connect('unrealize', self.disconnect_monitors)
        self.popover.set_parent(self.box)
        self.popover.set_has_arrow(False)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover.connect('closed', lambda *a: self.layer and LayerShell.set_keyboard_mode(
            self, LayerShell.KeyboardMode.NONE))

    # --- geometry -------------------------------------------------------------------------

    @staticmethod
    def monitor_count():
        return Gdk.Display.get_default().get_monitors().get_n_items()

    @staticmethod
    def pick_monitor(connector):
        monitors = Gdk.Display.get_default().get_monitors()
        items = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        for m in items:
            if connector and m.get_connector() == connector:
                return m
        return items[0] if items else None

    @staticmethod
    def width_for(monitor):
        geo = monitor.get_geometry() if monitor else None
        return min(1000, int(geo.width * 0.7)) if geo else 1000

    def dock(self):
        """Anchor the overlay for the position chosen in the menu. Floating, it covers the screen
        and the box moves inside it. Docked, it is a strip along one edge that the compositor
        keeps other windows out of, so maximised and snapped windows end where it begins."""
        position = self.position()
        self.edge = None if position == 'floating' else position
        self.span = None
        # docked, like a panel: next to the panels already there; floating, above everything,
        # fullscreen apps included
        LayerShell.set_layer(self, LayerShell.Layer.TOP if self.edge else LayerShell.Layer.OVERLAY)
        self.watch_fullscreen()
        self.touch_panels()
        for name in ('top', 'bottom', 'left', 'right'):
            LayerShell.set_anchor(self, getattr(LayerShell.Edge, name.upper()),
                                  self.edge is None or name == self.edge or name in ALONG[self.edge])
        if self.edge:
            LayerShell.auto_exclusive_zone_enable(self)
            self.add_css_class('docked')
        else:
            LayerShell.set_exclusive_zone(self, -1)  # float over everything, panels too
            self.remove_css_class('docked')
        self.seat_footer()
        self.set_cursor(Gdk.Cursor.new_from_name(self.cursor(), None))

    def position(self):
        """Where the captions go now: the menu's choice, or floating over a fullscreen app."""
        chosen = self.settings.get('position')
        return 'floating' if self.covered else chosen

    def watch_fullscreen(self):
        """While the captions are docked, have KWin say when an app on their screen is fullscreen.
        Once it stops, what it said no longer holds: docked again later, the captions would float
        over an app that may have closed meanwhile, until the new watch said otherwise."""
        if self.settings.get('position') == 'floating':
            self.app.fullscreen_watch.stop()
            self.covered = False
        elif self.monitor:
            g = self.monitor.get_geometry()
            self.app.fullscreen_watch.start(self.monitor.get_connector() or '', (g.x, g.y, g.width, g.height))
        else:
            self.app.fullscreen_watch.start('')

    def touch_panels(self):
        """Docked, the captions touch the panels on their edge: on Plasma those stop floating, as
        they do for a window that touches them (see PANELS_SCRIPT). Floating over a fullscreen
        app changes nothing: the panels are hidden then, and the captions dock again after."""
        chosen = self.settings.get('position')
        output = (self.monitor.get_connector() or '') if self.monitor else ''
        self.app.panel_float.dock(None if chosen == 'floating' else chosen, output)

    def set_covered(self, covered):
        """A fullscreen app on this screen opened or closed: float over it, or dock again."""
        if covered != self.covered and self.layer:
            self.covered = covered
            self.reposition()

    def seat_footer(self):
        """Floating, status messages get a row of their own under the captions. Docked, the strip
        must keep its size (the windows beside it would resize), so they show over its corner."""
        footer, docked = self.status_label, bool(self.edge)
        if docked and footer.get_parent() is self.box:
            self.box.remove(footer)
            self.rows_area.add_overlay(footer)
        elif not docked and footer.get_parent() is self.rows_area:
            self.rows_area.remove_overlay(footer)
            self.box.append(footer)
        footer.set_halign(Gtk.Align.END if docked else Gtk.Align.FILL)
        footer.set_valign(Gtk.Align.START if docked else Gtk.Align.FILL)
        footer.set_max_width_chars(48 if docked else -1)
        (footer.add_css_class if docked else footer.remove_css_class)('corner')

    def reposition(self):
        """Float or dock as the menu now says. Docked captions slide into their edge while the
        space they reserve shrinks with them, so maximised windows grow along (each app redraws
        as fast as it can and skips the sizes it misses); then they slide out of the new edge
        the same way. Floating captions fade instead. An open menu moves with them."""
        if not self.layer:
            return
        self.moving += 1
        token = self.moving
        for animation in self.moves:
            animation.pause()  # a move already under way stops where it is
        self.moves = []
        if not (self.get_mapped() and Adw.get_enable_animations(self)):
            self.box.set_opacity(1.0)
            return self.arrive(slide_in=False)

        def arrived():
            if token != self.moving:
                return
            self.arrive(slide_in=True)
            if self.edge:
                self.box.set_opacity(1.0)
                self.slide(0, self.drawer.full(), DOCK_MS, Adw.Easing.EASE_OUT_CUBIC, self.docked_fully)
            else:
                self.fade(self.box, 0.0, 1.0, FADE_IN_MS)

        if self.edge:
            self.drawer.edge = self.edge
            shown = self.drawer.full() if self.drawer.shown is None else self.drawer.shown
            self.slide(shown, 0, UNDOCK_MS, Adw.Easing.EASE_IN_CUBIC, arrived)
        else:
            self.fade(self.box, None, 0.0, FADE_OUT_MS, arrived)

    def docked_fully(self):
        """The slide out of the edge is over: the strip reserves its whole size again."""
        self.drawer.show_part(None, None)
        LayerShell.auto_exclusive_zone_enable(self)
        self.update_input()

    def arrive(self, slide_in):
        """Anchor and fit the captions at the new position; docked, slid away into the edge
        first when they are about to slide out of it."""
        self.dock()
        if self.edge and slide_in:
            self.drawer.show_part(self.edge, 0)
            LayerShell.set_exclusive_zone(self, 0)
        else:
            self.drawer.show_part(None, None)
        self.restyle()
        if self.popover.get_visible():
            self.point_menu(self.x + self.box_width // 2, self.y + self.box_height() // 2)

    def slide(self, start, end, ms, easing, done):
        """Show start..end px of the docked box over ms. Each frame the space the captions
        reserve, and the part that takes clicks, is just what shows, so maximised windows move
        with the slide; the window itself keeps its size."""
        def step(value):
            self.drawer.show_part(self.edge, value)
            LayerShell.set_exclusive_zone(self, round(value))
            self.update_input()
        animation = Adw.TimedAnimation.new(self, start, end, ms, Adw.CallbackAnimationTarget.new(step))
        animation.set_easing(easing)
        animation.connect('done', lambda *a: done())
        self.moves.append(animation)
        animation.play()

    def docked_part(self, w, h):
        """The part of a docked window the captions show: all of it, or during a slide only
        what has slid out of the edge."""
        away = self.drawer.hidden()
        return {'top': (0, 0, w, h - away), 'bottom': (0, away, w, h - away),
                'left': (0, 0, w - away, h), 'right': (away, 0, w - away, h)}[self.edge]

    def cursor(self):
        return 'default' if self.edge else 'grab'

    def dock_span(self):
        """The docked strip's length along its edge: what the compositor gave it beside any
        panels, or until it says, the whole screen's."""
        if self.span:
            return self.span
        geo = self.monitor.get_geometry() if self.monitor else None
        if not geo:
            return 1000
        return geo.width if self.edge in ('top', 'bottom') else geo.height

    def on_layout(self, surface, width, height):
        """The compositor sized the window. Docked, fit the captions to the strip it gave them."""
        span = (width if self.edge in ('top', 'bottom') else height) if self.edge else None
        if span and span != self.span and not self.resizing:
            self.span = span
            self.fit()
        self.update_input()

    def use_monitor(self, monitor):
        """Move to monitor, resizing the box and its text wrapping to fit it."""
        self.monitor = monitor
        self.span = None
        self.box_width = self.width_for(monitor)
        if self.layer and monitor:
            self.watch_fullscreen()
            self.touch_panels()
        self.watch_monitor()
        if self.layer and monitor:
            LayerShell.set_monitor(self, monitor)
        self.restyle()

    def on_monitors_changed(self, monitors, position, removed, added):
        """A screen was connected or disconnected: stay on a screen that exists, go back to the
        chosen screen when it is plugged in again (a laptop docked again), update the menu."""
        items = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        connector = self.settings.get('monitor')
        chosen = next((m for m in items if connector and m.get_connector() == connector), None)
        if chosen and chosen != self.monitor:
            self.use_monitor(chosen)
        elif self.monitor not in items:
            self.use_monitor(self.pick_monitor(connector))
        self.update_menu(self.layer and len(items) > 1)

    def update_menu(self, several_screens):
        """Set the menu, with the text size, background and text colour rows as buttons that
        keep it open. The size and background rows show their value between their buttons; the
        colour row shows the chosen colour's name, with a line of colour dots under it."""
        self.popover.set_menu_model(self.app.build_menu(several_screens, self.layer))
        self.values = {}
        for name, (title, *buttons) in STEPPERS.items():
            row = Gtk.Box(spacing=2, margin_start=MENU_INDENT, margin_end=6)
            row.append(Gtk.Label(label=title, xalign=0, hexpand=True))
            for tooltip, action, icon in buttons:
                button = Gtk.Button(icon_name=icon, action_name=action, tooltip_text=tooltip)
                button.add_css_class('flat')
                button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
                row.append(button)
                if not self.values.get(name):
                    self.values[name] = Gtk.Label(width_chars=5, css_classes=['dim-label', 'numeric'])
                    row.append(self.values[name])
            self.popover.add_child(row, name)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=MENU_INDENT,
                      margin_end=6, margin_top=6, margin_bottom=4)
        row = Gtk.Box()
        row.append(Gtk.Label(label='Text colour', xalign=0, hexpand=True))
        self.values['text-colour'] = Gtk.Label(css_classes=['dim-label'], margin_end=6)
        row.append(self.values['text-colour'])
        box.append(row)
        dots = Gtk.Box(spacing=1)
        for name, (label, _) in COLOURS.items():  # ringed, like a radio button, when chosen
            swatch = Gtk.Box(css_classes=['colour-swatch', f'colour-{name}'],
                             halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            button = Gtk.ToggleButton(child=swatch, action_name='app.colour',
                                      action_target=GLib.Variant('s', name), tooltip_text=label,
                                      css_classes=['flat', 'colour-dot'])
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            dots.append(button)
        box.append(dots)
        self.popover.add_child(box, 'text-colour')
        if self.layer:  # elsewhere the window manager places the window
            self.popover.add_child(self.position_row(), 'position')
        self.show_values()

    def refresh_menu(self):
        """Build the menu again, for an item whose label changed."""
        self.update_menu(self.layer and self.monitor_count() > 1)

    def position_row(self):
        """The Position row: the chosen position's name, and a little screen for each position
        showing where the captions go; like the colour dots, they keep the menu open."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=MENU_INDENT,
                      margin_end=6, margin_top=6, margin_bottom=4)
        row = Gtk.Box()
        row.append(Gtk.Label(label='Position', xalign=0, hexpand=True))
        self.values['position'] = Gtk.Label(css_classes=['dim-label'], margin_end=6)
        row.append(self.values['position'])
        box.append(row)
        icons = Gtk.Box(spacing=2)
        for name, label in POSITIONS.items():
            screen = Gtk.Box(css_classes=['screen'], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            strip = Gtk.Box(css_classes=['strip', 'across' if name in ('top', 'bottom') else
                                         'down' if name in ('left', 'right') else 'floating'],
                            hexpand=True, vexpand=name in ('left', 'right'),
                            halign={'left': Gtk.Align.START, 'right': Gtk.Align.END,
                                    'floating': Gtk.Align.CENTER}.get(name, Gtk.Align.FILL),
                            valign={'top': Gtk.Align.START, 'left': Gtk.Align.FILL,
                                    'right': Gtk.Align.FILL}.get(name, Gtk.Align.END))
            screen.append(strip)
            button = Gtk.ToggleButton(child=screen, action_name='app.position', tooltip_text=label,
                                      action_target=GLib.Variant('s', name), css_classes=['flat', 'position'])
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            icons.append(button)
        box.append(icons)
        return box

    def show_values(self):
        values = getattr(self, 'values', None)
        if values:
            values['text-size'].set_label(f"{self.settings.get('size')} px")
            values['background'].set_label(f"{round(self.settings.get('opacity') * 100)}%")
            values['text-colour'].set_label(COLOURS[self.settings.get('colour')][0])
            if 'position' in values:
                values['position'].set_label(POSITIONS[self.settings.get('position')])

    def watch_screens(self, *args):
        """Follow screens being connected or disconnected, and the current screen's geometry."""
        if self._monitors_handler is None:
            self._monitors_handler = self._monitors.connect('items-changed', self.on_monitors_changed)
        self.watch_monitor()

    def watch_monitor(self):
        for monitor, handler in self._monitor_handlers:
            monitor.disconnect(handler)
        self._monitor_handlers = []
        if self.monitor:
            for prop in ('geometry', 'scale-factor'):
                handler = self.monitor.connect(f'notify::{prop}', self.on_monitor_geometry)
                self._monitor_handlers.append((self.monitor, handler))

    def on_monitor_geometry(self, monitor, pspec):
        if monitor == self.monitor:
            self.box_width = self.width_for(monitor)
            self.restyle()
            if self.layer:  # KWin 5 finds the screen by its area
                self.watch_fullscreen()

    def disconnect_monitors(self, *args):
        for monitor, handler in self._monitor_handlers:
            monitor.disconnect(handler)
        self._monitor_handlers = []
        if self._monitors_handler is not None:
            self._monitors.disconnect(self._monitors_handler)
            self._monitors_handler = None

    def box_height(self):
        if self.edge in ('left', 'right'):  # the column runs the strip's whole height
            return self.dock_span()
        return self.caption_height() + (self.status_height if self.text and self.status and not self.edge else 0)

    def caption_height(self):
        return self.rows * self.row_height + 2 * PAD_Y

    def clamp(self, x, y):
        """x, y on the monitor (None: centred near the bottom), keeping the whole box on screen."""
        geo = self.monitor.get_geometry()
        w, h = self.box_width, self.box_height()
        x = (geo.width - w) // 2 if x is None else x
        y = geo.height - h - BOTTOM_GAP if y is None else y
        return max(0, min(int(x), geo.width - w)), max(0, min(int(y), geo.height - h))

    def place(self, x=None, y=None):
        """Put the caption box at x, y on its monitor (docked, it fills the strip)."""
        if not self.layer or not self.monitor or self.edge:
            self.x = self.y = 0
            if self.edge:
                self.stage.move(0, 0)
                self.update_input()
            return
        self.x, self.y = self.clamp(x, y)
        self.stage.move(self.x, self.y)
        self.update_input()

    def update_input(self):
        """Only the caption box takes pointer input; the rest of the overlay clicks through."""
        surface = self.get_surface()
        if self.layer and surface:
            if self.edge:
                x, y, w, h = self.docked_part(surface.get_width(), surface.get_height())
            else:
                x, y, w, h = self.x, self.y, self.box_width, self.box_height()
            surface.set_input_region(cairo.Region(cairo.RectangleInt(int(x), int(y), max(0, int(w)), max(0, int(h)))))

    def next_monitor(self):
        monitors = Gdk.Display.get_default().get_monitors()
        items = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        if len(items) < 2 or not self.layer:
            return
        monitor = items[(items.index(self.monitor) + 1) % len(items)] if self.monitor in items else items[0]
        self.settings.set('monitor', monitor.get_connector() or '')
        self.settings.set('x', -1)
        self.settings.set('y', -1)
        self.use_monitor(monitor)

    def reset_position(self):
        self.settings.set('x', -1)
        self.settings.set('y', -1)
        self.place()

    def reset_size(self):
        """The floating box's size, or docked, the strip's."""
        if self.edge:
            self.settings.set('dock_rows', ROWS)
            self.settings.set('dock_width', -1)
        else:
            self.settings.set('width', -1)
            self.settings.set('rows', ROWS)
            self.rows = ROWS
            self.box_width = self.width_for(self.monitor)
        self.restyle()

    def max_rows(self):
        """As many rows as fit on the screen."""
        geo = self.monitor.get_geometry() if self.monitor else None
        if not geo:
            return 20
        height = geo.height // 2 if self.edge in ('top', 'bottom') else geo.height  # a strip leaves room for windows
        return max(1, (height - 2 * PAD_Y - self.status_height) // max(1, self.row_height))

    def restyle(self):
        """Apply the text size, background and text colour, and fit the box. A new background or
        colour on its own moves nothing, so the box is not fitted again: that wrapped the rows
        afresh, moving words between them, and stopped the glide and the words arriving."""
        px, alpha = self.settings.get('size'), self.settings.get('opacity')
        colour = COLOURS[self.settings.get('colour')][1]
        font = self.settings.get('font')
        family = f'font-family: {css_font(font)};' if font.strip() else ''
        before, self.look = getattr(self, 'look', None), (px, font, alpha, colour)
        recoloured = before is not None and before[:2] == self.look[:2] and before != self.look
        swatches = ''.join(f'window.nowcaption .colour-{name} {{ background: {rgb}; }}\n'
                           for name, (_, rgb) in COLOURS.items())
        self.css.load_from_string(f"""
            window.nowcaption {{ background: transparent; }}
            window.nowcaption .caption-box {{ background: rgba(0, 0, 0, {alpha}); border-radius: 14px;
                                             transition: background-color {CHANGE_MS}ms ease-out; }}
            window.nowcaption .caption-text {{ color: {colour}; font-size: {px}px; font-weight: 500; {family}
                                              transition: color {CHANGE_MS}ms ease-out; }}
            .caption-preview {{ background: rgba(0, 0, 0, {alpha}); border-radius: 14px; }}
            .caption-preview .caption-text {{ color: {colour}; font-size: {min(px, Preview.MAX_SIZE)}px;
                                             font-weight: 500; {family} transition: color {CHANGE_MS}ms ease-out; }}
            window.nowcaption .caption-status {{ color: #cccccc; font-size: {max(14, round(px * 0.55))}px;
                                                padding: 0 {PAD_X}px {PAD_Y}px; }}
            window.nowcaption.docked .caption-box {{ border-radius: 0; }}
            window.nowcaption .caption-status.corner {{ background: rgba(0, 0, 0, 0.85); border-radius: 8px;
                                                       padding: 2px 10px; margin: 6px; }}
            window.nowcaption .screen {{ min-width: 22px; min-height: 15px; border-radius: 3px;
                                        border: 1.5px solid alpha(currentColor, 0.7); }}
            window.nowcaption .screen .strip {{ background: currentColor; border-radius: 1px; }}
            window.nowcaption .screen .strip.across {{ min-height: 4px; }}
            window.nowcaption .screen .strip.down {{ min-width: 5px; }}
            window.nowcaption .screen .strip.floating {{ min-width: 11px; min-height: 4px; margin-bottom: 2px; }}
            window.nowcaption button.position {{ padding: 5px 7px; min-height: 0; min-width: 0; }}
            window.nowcaption button.position:checked {{ background: alpha(@accent_bg_color, 0.25); }}
            window.nowcaption button.position:checked .screen {{ color: @accent_color; }}
            window.nowcaption .colour-swatch {{ min-width: 18px; min-height: 18px; border-radius: 99px;
                                               border: 1px solid rgba(0, 0, 0, 0.4); }}
            window.nowcaption button.colour-dot {{ padding: 3px; min-width: 0; min-height: 0;
                                                  border-radius: 99px; }}
            window.nowcaption button.colour-dot:checked {{ background: none; }}
            window.nowcaption button.colour-dot:checked .colour-swatch {{
                box-shadow: 0 0 0 2px @popover_bg_color, 0 0 0 3px currentColor; }}
            {swatches}""")
        self.show_values()
        if not recoloured:
            self.fit()

    def font_heights(self):
        """The height of a caption row and of the status row, in the fonts the labels have now."""
        return (self.label.create_pango_layout('Ag').get_pixel_size()[1],
                self.status_label.create_pango_layout('Ag').get_pixel_size()[1] + PAD_Y)

    def on_fonts(self):
        """GTK applied new CSS to a label. A new text size reaches the labels only now, a frame
        after restyle() measured them, so size the box again if the fonts measure differently."""
        if hasattr(self, 'status_height') and self.font_heights() != (self.row_height, self.status_height):
            self.fit()

    def fit(self):
        """Size the box for the caption font and the position. Floating: exactly self.rows rows at
        its width and place, and a new text size keeps its bottom edge where it was. Docked at
        the top or bottom: the strip's rows across the whole edge. Docked at a side: a column of
        its width with as many rows as the strip's height holds."""
        before = self.fitted
        self.row_height, self.status_height = self.font_heights()
        geo = self.monitor.get_geometry() if self.monitor else None
        if self.edge in ('top', 'bottom'):
            self.box_width = self.dock_span()
            self.rows = max(1, min(self.settings.get('dock_rows'), self.max_rows()))
        elif self.edge:  # a width edited by hand is kept to what dragging the edge allows
            width = self.settings.get('dock_width')
            widest = int(geo.width * 0.6) if geo else 2000
            self.box_width = max(MIN_WIDTH, min(width, widest)) if width > 0 else self.dock_width_for(self.monitor)
            self.rows = max(1, (self.dock_span() - 2 * PAD_Y) // max(1, self.row_height))
        else:  # a size the user dragged to, kept within the current screen
            width = self.settings.get('width')
            if width > 0:
                self.box_width = min(width, geo.width) if geo else width
            elif before and before[0]:  # back from docked
                self.box_width = self.width_for(self.monitor)
            self.rows = max(1, min(self.settings.get('rows'), self.max_rows()))
        self.relayout()
        x, y = self.settings.get('x'), self.settings.get('y')
        floated = before and before[0] is None and self.edge is None  # floating before and now
        grown = self.caption_height() - before[1] if self.layer and floated else 0
        if grown and y >= 0:  # the default place keeps its bottom edge already
            self.place(x if x >= 0 else None, self.y - grown)
            self.settings.set('y', self.y)
        else:
            self.place(x if x >= 0 else None, y if y >= 0 else None)
        self.fitted = (self.edge, self.caption_height())
        self.render()

    @staticmethod
    def dock_width_for(monitor):
        geo = monitor.get_geometry() if monitor else None
        return max(MIN_WIDTH, min(480, geo.width // 4)) if geo else 480

    def relayout(self):
        """Wrap the text to the box width and fix the caption at exactly self.rows rows."""
        self.layout = self.label.create_pango_layout('')
        self.layout.set_width((self.box_width - 2 * PAD_X) * Pango.SCALE)
        self.layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        # Docked, the compositor sets the length along the edge (what panels leave): wrap to it,
        # but never ask for it, or the strip would reach under the panels.
        across = self.edge in ('top', 'bottom')
        self.caption.set_size_request(-1 if across else self.box_width - 2 * PAD_X, -1)
        self.caption.configure(self.layout, self.rows, self.row_height, flexible=self.edge in ('left', 'right'))
        self.status_label.set_size_request(-1 if self.edge else self.box_width, -1)
        self.stage.fill = bool(self.edge)
        if self.edge:  # let the strip shrink with the box too, not only grow
            self.set_default_size(1, 1)

    # --- text -----------------------------------------------------------------------------

    def show_text(self, text):
        """New caption passage. It replaces a persistent status message such as 'Listening…'."""
        self.text = text
        if text and self.status and not self.status_timer and not self.status_sticky:
            self.status = ''
        self.render()
        return False

    def show_status(self, message, seconds=None, sticky=False):
        """Show status below existing captions, or in the empty caption box."""
        if self.status_timer:
            GLib.source_remove(self.status_timer)
            self.status_timer = None
        self.status_scope = None
        if self.failures and not seconds and not sticky:
            self.end_status()  # replayed captions and a new Listening notice do not prove recovery
            return
        self.status = message
        self.status_sticky = sticky
        if seconds:
            self.status_timer = GLib.timeout_add_seconds(seconds, self.end_status)
        self.render()

    def show_failure(self, scope, message):
        """Retain a runtime problem until that component actually recovers."""
        self.failures.pop(scope, None)
        self.failures[scope] = message
        self.show_status(message, sticky=True)
        self.status_scope = scope

    def clear_failure(self, scope):
        self.failures.pop(scope, None)
        if self.status_scope == scope:
            self.end_status()

    def end_status(self):
        self.status_timer, self.status = None, ''
        self.status_sticky = False
        self.status_scope = None
        if self.failures:
            self.status_scope = next(reversed(self.failures))
            self.status = self.failures[self.status_scope]
            self.status_sticky = True
        self.render()
        return False

    def render(self):
        # the text flows on; only the last rows show, so the newest words are always visible
        waiting = not self.text and self.status.endswith('…') and self.status not in self.failures.values()
        self.caption.show(self.text or self.status, status=not self.text, waiting=waiting)
        self.show_footer(self.status if self.text else '')
        # The optional status row participates in overlay clamping and pointer input.
        self.place(self.x, self.y)

    def show_footer(self, message):
        """The status row under the captions. It fades in, and fades out before the box shrinks."""
        footer = self.status_label
        if message:
            footer.set_text(message)
            footer.set_tooltip_text(message)
            footer.set_visible(True)
            if not self.footer_shown:
                self.fade(footer, None if self.footer_hiding else 0.0, 1.0, STATUS_MS)
            self.footer_hiding = False
        elif self.footer_shown:
            def hidden():
                self.footer_hiding = False
                footer.set_visible(False)
                self.place(self.x, self.y)
            self.footer_hiding = True
            self.fade(footer, None, 0.0, STATUS_MS, hidden)
        elif not self.footer_hiding:
            footer.set_visible(False)
        self.footer_shown = bool(message)

    def fade(self, widget, start, end, ms, done=None):
        """Fade widget's opacity from start (None: from where it is) to end, then call done.
        Instant while the widget is hidden or the desktop turns animations off."""
        old = self.fades.pop(widget, None)
        if old:
            old.pause()  # replaced: its done never runs
        if start is not None:
            widget.set_opacity(start)
        animation = Adw.TimedAnimation.new(widget, widget.get_opacity(), end, ms,
                                           Adw.PropertyAnimationTarget.new(widget, 'opacity'))
        animation.set_easing(Adw.Easing.EASE_OUT_CUBIC if end > widget.get_opacity() else Adw.Easing.EASE_IN_CUBIC)

        def finished(animation):
            if self.fades.get(widget) is animation:
                del self.fades[widget]
            if done:
                done()
        animation.connect('done', finished)
        self.fades[widget] = animation
        animation.play()

    def fade_in(self, box):
        """The caption box fades in when it first appears, not when it moves to another screen."""
        if not self.faded_in:
            self.faded_in = True
            self.fade(box, 0.0, 1.0, FADE_IN_MS)

    def save_png(self, path):
        """Render the window exactly as drawn, for store screenshots."""
        w, h = self.box.get_width(), self.box.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(self.box).snapshot(snap, w, h)
        node = snap.to_node()
        if node:
            self.get_renderer().render_texture(node, None).save_to_png(path)
        return False

    # --- moving and resizing ------------------------------------------------------------------

    def edges_at(self, x, y):
        """Which edges of the box the pointer is on, as 'n', 'se', ...; '' inside it.
        A normal window cannot move itself, so there only the right and bottom edges resize.
        Docked, only the edge between the captions and the windows beside them resizes."""
        if self.edge:
            w, h = self.get_width(), self.get_height()
            inner = INNER[self.edge]
            near = {'n': y < EDGE, 's': y >= h - EDGE, 'w': x < EDGE, 'e': x >= w - EDGE}[inner]
            return inner if near and 0 <= x < w and 0 <= y < h else ''
        bx, by = (self.x, self.y) if self.layer else (0, 0)
        x, y = x - bx, y - by
        w, h = self.box_width, self.box_height()
        if not (0 <= x < w and 0 <= y < h):
            return ''
        ns = 'n' if y < EDGE and self.layer else 's' if y >= h - EDGE else ''
        ew = 'w' if x < EDGE and self.layer else 'e' if x >= w - EDGE else ''
        return ns + ew

    def on_motion(self, controller, x, y):
        if not (self.dragging or self.resizing):
            self.set_cursor(Gdk.Cursor.new_from_name(CURSORS.get(self.edges_at(x, y), self.cursor()), None))

    def hold_cursor(self, x, y, name):
        """Show cursor name until the drag ends. Once the pointer leaves the widget it pressed,
        GTK shows that widget's own cursor rather than the window's, and a docked strip moves
        under the pointer as it resizes, so the pressed widget gets the same cursor."""
        cursor = Gdk.Cursor.new_from_name(name, None)
        self.set_cursor(cursor)
        self.held = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if self.held is self:
            self.held = None
        if self.held:
            self.held.set_cursor(cursor)

    def on_drag_begin(self, gesture, x, y):
        edges = self.edges_at(x, y)
        if self.edge:  # docked: only the inner edge resizes, and nothing moves
            if edges:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.resizing = (edges,)
                self.hold_cursor(x, y, CURSORS[edges])
            return
        if edges:  # remember which edges, and where the box's edges were
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.resizing = (edges, self.x, self.y, self.x + self.box_width, self.y + self.caption_height())
            self.hold_cursor(x, y, CURSORS[edges])
            return
        self.hold_cursor(x, y, 'grabbing')
        if self.layer:  # x, y are overlay (monitor) coordinates; remember where the box was grabbed
            self.dragging = (x - self.x, y - self.y)
            return
        if isinstance(self.get_surface(), Gdk.Toplevel):  # the window manager moves normal windows
            if gesture.get_current_sequence() is not None:  # a finger: held still, it opens the menu instead
                self.move_from = (x, y)
                return
            self.begin_move(gesture, x, y)

    def begin_move(self, gesture, x, y):
        """Have the window manager move the window from (x, y), where the press is now."""
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.get_surface().begin_move(gesture.get_device(), gesture.get_current_button(), x, y,
                                      gesture.get_current_event_time())

    def on_drag_update(self, gesture, dx, dy):
        if self.move_from:  # a finger moves the window once it travels as far as a drag must
            if math.hypot(dx, dy) >= self.get_settings().props.gtk_dnd_drag_threshold:
                x, y = self.move_from
                self.move_from = None
                self.begin_move(gesture, x + dx, y + dy)
            return
        if self.resizing and self.edge:
            ok, px, py = gesture.get_point(None)
            return ok and self.resize_dock(px, py)
        if self.resizing:
            return self.resize_by(dx, dy)
        if not self.dragging:
            return
        ok, px, py = gesture.get_point(None)
        if ok:
            self.place(px - self.dragging[0], py - self.dragging[1])

    def resize_by(self, dx, dy):
        """Move the dragged edges by dx, dy from where they started; the other edges stay put."""
        edges, left, top, right, bottom = self.resizing
        geo = self.monitor.get_geometry() if self.monitor else None
        limit = (geo.width if self.layer else left + geo.width) if geo else left + 10000
        if 'w' in edges:
            left = max(0, min(left + dx, right - MIN_WIDTH))
        if 'e' in edges:
            right = max(left + MIN_WIDTH, min(right + dx, limit))
        rows = self.rows
        if 'n' in edges or 's' in edges:
            height = bottom - top + (dy if 's' in edges else -dy)
            rows = max(1, min(round((height - 2 * PAD_Y) / max(1, self.row_height)), self.max_rows()))
        width = int(right - left)
        if (width, rows) != (self.box_width, self.rows):
            self.box_width, self.rows = width, rows
            self.relayout()
        if self.layer:  # render() places the box, clamped to the screen
            self.x = int(left)
            self.y = int(bottom - self.caption_height() if 'n' in edges else top)
        self.render()

    def resize_dock(self, x, y):
        """Docked: the inner edge follows the pointer at x, y in the strip; the edge against the
        screen stays, and the windows beside the captions follow. Measured from that outer edge
        each time, since the strip itself moves under the pointer as it grows."""
        w, h = self.get_width(), self.get_height()
        if self.edge in ('top', 'bottom'):
            height = y if self.edge == 'top' else h - y
            rows = max(1, min(round((height - 2 * PAD_Y) / max(1, self.row_height)), self.max_rows()))
            if rows != self.rows:
                self.rows = rows
                self.relayout()
        else:
            geo = self.monitor.get_geometry() if self.monitor else None
            width = max(MIN_WIDTH, min(int(x if self.edge == 'left' else w - x), int(geo.width * 0.6) if geo else 2000))
            if width != self.box_width:
                self.box_width = width
                self.relayout()
        self.render()

    def on_drag_end(self, gesture, dx, dy):
        self.move_from = None
        self.set_cursor(Gdk.Cursor.new_from_name(self.cursor(), None))
        if self.held:
            self.held.set_cursor(None)
            self.held = None
        if self.resizing and self.edge:
            self.resizing = None
            if self.edge in ('top', 'bottom'):
                self.settings.set('dock_rows', self.rows)
            else:
                self.settings.set('dock_width', self.box_width)
            return
        if self.resizing:
            self.resizing = None
            self.settings.set('width', self.box_width)
            self.settings.set('rows', self.rows)
            self.settings.set('x', self.x)
            self.settings.set('y', self.y)
            return
        if not self.dragging:
            return
        self.dragging = None
        self.settings.set('x', self.x)
        self.settings.set('y', self.y)
        if os.environ.get('NOWCAPTION_DEBUG_DRAG'):
            print('drag end', self.x, self.y, flush=True)

    def point_menu(self, x, y):
        """Point the menu at x, y in the window, opening toward the room on the screen: away
        from a docked strip's edge, beside a docked column, and upwards from captions low on
        the screen. KWin squeezes a layer-shell menu that does not fit, or lets it run off the
        screen, rather than flipping it."""
        rect = Gdk.Rectangle()  # the menu belongs to the box, so use box coordinates
        w, h = self.box_width, self.box_height()
        side = Gtk.PositionType.BOTTOM
        if self.edge == 'bottom':
            side, (rect.x, rect.y, rect.width, rect.height) = Gtk.PositionType.TOP, (int(x), 0, 1, 1)
        elif self.edge == 'top':
            rect.x, rect.y, rect.width, rect.height = int(x), h - 1, 1, 1
        elif self.edge in ('left', 'right'):
            side = Gtk.PositionType.RIGHT if self.edge == 'left' else Gtk.PositionType.LEFT
            rect.x, rect.y, rect.width, rect.height = w - 1 if self.edge == 'left' else 0, 0, 1, h
        else:
            bx, by = (self.x, self.y) if self.layer else (0, 0)
            geo = self.monitor.get_geometry() if self.layer and self.monitor else None
            if geo and y > geo.height / 2:  # the overlay covers the screen, so y is on it
                side = Gtk.PositionType.TOP
            rect.x, rect.y, rect.width, rect.height = int(x - bx), int(y - by), 1, 1
        self.popover.set_position(side)
        self.popover.set_pointing_to(rect)

    def on_menu(self, gesture, n, x, y):
        if self.layer:  # let the menu take keyboard input while it is open
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)
        self.point_menu(x, y)
        if self.layer:  # let the compositor apply the keyboard mode first, or KWin closes the menu at once
            GLib.timeout_add(60, lambda: self.popover.popup() and False)
        else:
            self.popover.popup()

    def on_long_press(self, gesture, x, y):
        """A finger held still on the captions opens the menu there, as a right-click does. A
        finger that moves first drags them, or resizes them from an edge, and GTK cancels the
        long press; taking the press here ends a move that began with it. Without layer shell,
        the window manager moves the window only once the finger travels (on_drag_update)."""
        if self.resizing:  # an edge took the press
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.on_menu(gesture, 1, x, y)
