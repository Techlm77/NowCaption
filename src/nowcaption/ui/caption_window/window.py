"""The caption box's window, floating where it is dragged or docked along an edge of the screen
(layer shell), with its menu and resizing. Stage and Drawer (stage.py) place and slide the box."""
import cairo
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ...style import BOTTOM_GAP, COLOURS, FADE_IN_MS, MIN_WIDTH, PAD_X, PAD_Y, ROWS, STATUS_MS  # noqa: E402
from ..caption_text import CaptionText, StyledLabel  # noqa: E402
from . import layer_shell  # noqa: E402
from .docking import Docking  # noqa: E402
from .menu import CaptionMenu  # noqa: E402
from .pointer import Pointer  # noqa: E402
from .stage import Drawer, Stage  # noqa: E402
from .stylesheet import caption_css  # noqa: E402


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
        self.layer = layer_shell.supported()
        self.pointer = Pointer(self)  # moving and resizing the box
        self.rows = self.settings.get('rows')
        self.text, self.status, self.status_timer = '', '', None
        self.status_sticky = False
        self.failures, self.status_scope = {}, None
        self.monitor = self.pick_monitor(self.settings.get('monitor'))
        self.box_width = self.width_for(self.monitor)
        self.edge = self.span = self.fitted = None  # see Docking.dock(), on_layout() and fit()
        self.docking = Docking(self)  # floating or docked, and moving between them

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
        self.stage = Stage(self.drawer)  # floating, the box moves inside the window while dragged
        self.set_child(self.stage)
        self.menu = CaptionMenu(self)

        if self.layer:
            shell = layer_shell.LayerShell
            shell.init_for_window(self)
            shell.set_layer(self, shell.Layer.OVERLAY)
            shell.set_namespace(self, 'nowcaption')
            shell.set_keyboard_mode(self, shell.KeyboardMode.NONE)
            if self.monitor:
                shell.set_monitor(self, self.monitor)
            self.set_resizable(True)  # or the compositor cannot stretch it across the edges it is anchored to
            self.docking.dock()
            self.connect('realize', lambda *a: self.get_surface().connect('layout', self.on_layout))
        self.restyle()

        drag = Gtk.GestureDrag()  # primary button
        drag.connect('drag-begin', self.pointer.on_drag_begin)
        drag.connect('drag-update', self.pointer.on_drag_update)
        drag.connect('drag-end', self.pointer.on_drag_end)
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()  # show a resize cursor over the edges
        motion.connect('motion', self.pointer.on_motion)
        self.add_controller(motion)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect('pressed', self.menu.on_menu)
        self.add_controller(click)
        hold = Gtk.GestureLongPress(touch_only=True)  # a touchscreen's right-click, see Pointer.on_long_press
        hold.connect('pressed', self.pointer.on_long_press)
        self.add_controller(hold)
        self.set_cursor(Gdk.Cursor.new_from_name(self.pointer.cursor(), None))

        self._monitor_handlers, self._monitors_handler = [], None
        self._monitors = Gdk.Display.get_default().get_monitors()
        self.watch_screens()
        # gtk4-layer-shell unrealizes and maps the window again whenever its screen changes,
        # so watch again on realize; destroying the window unrealizes it for good.
        self.connect('realize', self.watch_screens)
        self.connect('unrealize', self.disconnect_monitors)

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

    def set_covered(self, covered):
        """A fullscreen app on this screen opened or closed: float over it, or dock again."""
        self.docking.set_covered(covered)

    def reposition(self):
        """Float or dock as the menu now says (see Docking.reposition)."""
        self.docking.reposition()

    def touch_panels(self):
        """Docked, touch the panels on the captions' edge again, as the captions show again."""
        self.docking.touch_panels()

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
        if span and span != self.span and not self.pointer.resizing:
            self.span = span
            self.fit()
        self.update_input()

    def use_monitor(self, monitor):
        """Move to monitor, resizing the box and its text wrapping to fit it."""
        self.monitor = monitor
        self.span = None
        self.box_width = self.width_for(monitor)
        if self.layer and monitor:
            self.docking.watch_fullscreen()
            self.docking.touch_panels()
        self.watch_monitor()
        if self.layer and monitor:
            layer_shell.LayerShell.set_monitor(self, monitor)
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
        self.menu.update_menu(self.layer and len(items) > 1)

    @property
    def popover(self):
        """The caption box's menu, which the app closes when it hides the captions."""
        return self.menu.popover

    def refresh_menu(self):
        """Build the menu again, for an item whose label changed."""
        self.menu.refresh_menu()

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
                self.docking.watch_fullscreen()

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
                x, y, w, h = self.docking.docked_part(surface.get_width(), surface.get_height())
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
        before, self.look = getattr(self, 'look', None), (px, font, alpha, colour)
        recoloured = before is not None and before[:2] == self.look[:2] and before != self.look
        self.css.load_from_string(caption_css(px, font, alpha, colour))
        self.menu.show_values()
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
