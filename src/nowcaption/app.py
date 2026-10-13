"""NowCaption: on-device live captions for anything your computer plays, or your microphone."""
import logging
import os
import sys
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
import cairo  # noqa: E402
gi.require_version('Pango', '1.0')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

try:
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None

from . import __version__, audio, engine  # noqa: E402

APP_ID = 'io.github.Techlm77.NowCaption'
LOG = logging.getLogger('nowcaption')
SIZES = [18, 22, 26, 30, 36, 42, 50, 60]
ROWS = 2              # default caption rows, like broadcast subtitles; drag an edge for more
PAD_X, PAD_Y = 22, 12
BOTTOM_GAP = 80       # default distance from the bottom of the screen
EDGE = 10             # width of the band along the box's edges that resizes instead of moving
MIN_WIDTH = 240
CURSORS = {'n': 'n-resize', 's': 's-resize', 'e': 'e-resize', 'w': 'w-resize',
           'ne': 'ne-resize', 'nw': 'nw-resize', 'se': 'se-resize', 'sw': 'sw-resize'}
# Rows of − / + buttons in the menu. They are real buttons rather than menu items, because
# GTK closes a menu when one of its items is chosen, and these are pressed several times.
STEPPERS = {
    'text-size': ('Text size', ('Smaller text', 'app.smaller', 'zoom-out-symbolic'),
                  ('Larger text', 'app.larger', 'zoom-in-symbolic')),
    'background': ('Background', ('More see-through', 'app.lighter', 'value-decrease-symbolic'),
                   ('More solid', 'app.darker', 'value-increase-symbolic')),
}
DEFAULTS = {'source': 'desktop', 'size': 30, 'opacity': 0.78, 'x': -1, 'y': -1, 'monitor': '',
            'width': -1, 'rows': ROWS, 'music_filter': 0}
LIMITS = {'size': (SIZES[0], SIZES[-1]), 'opacity': (0.2, 1.0), 'rows': (1, 100), 'music_filter': (0, 1)}
CHOICES = {'source': ('desktop', 'mic')}


class Settings:
    """Small key file in the user's config directory."""

    def __init__(self):
        self.path = Path(GLib.get_user_config_dir()) / 'nowcaption' / 'settings.ini'
        self.kf = GLib.KeyFile()
        try:
            self.kf.load_from_file(str(self.path), GLib.KeyFileFlags.NONE)
        except GLib.Error:
            pass

    def get(self, key):
        """The stored value, brought back into range if the file was edited by hand."""
        default = DEFAULTS[key]
        try:
            if isinstance(default, int):
                value = self.kf.get_integer('main', key)
            elif isinstance(default, float):
                value = self.kf.get_double('main', key)
            else:
                value = self.kf.get_string('main', key)
        except GLib.Error:
            return default
        if key in LIMITS:
            low, high = LIMITS[key]
            value = max(low, min(high, value))
        if key == 'width' and 0 < value < MIN_WIDTH:
            value = MIN_WIDTH
        if key in CHOICES and value not in CHOICES[key]:
            value = default
        return value

    def set(self, key, value):
        """Remember value; if the file cannot be written it still applies until the app quits."""
        {int: self.kf.set_integer, float: self.kf.set_double}.get(type(DEFAULTS[key]), self.kf.set_string)('main', key, value)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.kf.save_to_file(str(self.path))
        except (OSError, GLib.Error) as e:
            LOG.warning('Could not save settings to %s: %s', self.path, e)


def last_rows(layout, text, rows):
    """The last `rows` wrapped lines of text, as Pango lays them out."""
    layout.set_text(text, -1)
    data = text.encode()
    lines = [data[l.start_index:l.start_index + l.length].decode(errors='ignore').strip()
             for l in layout.get_lines_readonly()]
    return '\n'.join(lines[-rows:])


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
        self.dragging = self.resizing = None
        self.rows = self.settings.get('rows')
        self.text, self.status, self.status_timer = '', '', None
        self.status_sticky = False
        self.failures, self.status_scope = {}, None
        self.monitor = self.pick_monitor(self.settings.get('monitor'))
        self.box_width = self.width_for(self.monitor)

        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.add_css_class('nowcaption')
        self.label = Gtk.Label(xalign=0, yalign=0, wrap=False, single_line_mode=False,
                               accessible_role=Gtk.AccessibleRole.STATUS)
        self.label.add_css_class('caption-text')
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.add_css_class('caption-box')
        self.box.append(self.label)
        self.status_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                      single_line_mode=True, accessible_role=Gtk.AccessibleRole.STATUS)
        self.status_label.add_css_class('caption-status')
        self.status_label.set_visible(False)
        self.box.append(self.status_label)
        self.fixed = Gtk.Fixed()  # lets the box move inside the window while it is dragged
        self.fixed.put(self.box, 0, 0)
        self.set_child(self.fixed)

        if self.layer:
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_namespace(self, 'nowcaption')
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            LayerShell.set_exclusive_zone(self, -1)
            if self.monitor:
                LayerShell.set_monitor(self, self.monitor)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            self.connect('realize', lambda *a: self.get_surface().connect('layout', lambda *a: self.update_input()))
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
        self.set_cursor(Gdk.Cursor.new_from_name('grab', None))

        self.popover = Gtk.PopoverMenu()
        self.update_menu(self.layer and self.monitor_count() > 1)
        self._monitor_handlers = []
        self._monitors = Gdk.Display.get_default().get_monitors()
        self._monitors_handler = self._monitors.connect('items-changed', self.on_monitors_changed)
        self.watch_monitor()
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

    def use_monitor(self, monitor):
        """Move to monitor, resizing the box and its text wrapping to fit it."""
        self.monitor = monitor
        self.box_width = self.width_for(monitor)
        self.watch_monitor()
        if self.layer and monitor:
            LayerShell.set_monitor(self, monitor)
        self.restyle()

    def on_monitors_changed(self, monitors, position, removed, added):
        """A screen was connected or disconnected: stay on a screen that exists, update the menu."""
        items = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        if self.monitor not in items:
            self.use_monitor(self.pick_monitor(self.settings.get('monitor')))
        self.update_menu(self.layer and len(items) > 1)

    def update_menu(self, several_screens):
        """Set the menu, with the text size and background rows as buttons that keep it open."""
        self.popover.set_menu_model(self.app.build_menu(several_screens))
        for name, (title, *buttons) in STEPPERS.items():
            row = Gtk.Box(spacing=4, margin_start=12, margin_end=6)
            row.append(Gtk.Label(label=title, xalign=0, hexpand=True))
            for tooltip, action, icon in buttons:
                button = Gtk.Button(icon_name=icon, action_name=action, tooltip_text=tooltip)
                button.add_css_class('flat')
                button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
                row.append(button)
            self.popover.add_child(row, name)

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

    def disconnect_monitors(self, *args):
        for monitor, handler in self._monitor_handlers:
            monitor.disconnect(handler)
        self._monitor_handlers = []
        if self._monitors_handler is not None:
            self._monitors.disconnect(self._monitors_handler)
            self._monitors_handler = None

    def box_height(self):
        return self.caption_height() + (self.status_height if self.text and self.status else 0)

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
        """Put the caption box at x, y on its monitor."""
        if not self.layer or not self.monitor:
            self.x = self.y = 0
            return
        self.x, self.y = self.clamp(x, y)
        self.fixed.move(self.box, self.x, self.y)
        self.update_input()

    def update_input(self):
        """Only the caption box takes pointer input; the rest of the overlay clicks through."""
        surface = self.get_surface()
        if self.layer and surface:
            surface.set_input_region(cairo.Region(cairo.RectangleInt(
                self.x, self.y, self.box_width, self.box_height())))

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
        return max(1, (geo.height - 2 * PAD_Y - self.status_height) // max(1, self.row_height))

    def restyle(self):
        px, alpha = self.settings.get('size'), self.settings.get('opacity')
        self.css.load_from_string(f"""
            window.nowcaption {{ background: transparent; }}
            window.nowcaption .caption-box {{ background: rgba(0, 0, 0, {alpha}); border-radius: 14px; }}
            window.nowcaption .caption-text {{ color: #ffffff; font-size: {px}px; font-weight: 500;
                                              padding: {PAD_Y}px {PAD_X}px; }}
            window.nowcaption .caption-status {{ color: #cccccc; font-size: {max(14, round(px * 0.55))}px;
                                                padding: 0 {PAD_X}px {PAD_Y}px; }}""")
        # measure one row in the caption font, then fix the box at exactly ROWS rows
        probe = self.label.create_pango_layout('Ag')
        self.row_height = probe.get_pixel_size()[1]
        self.status_height = self.status_label.create_pango_layout('Ag').get_pixel_size()[1] + PAD_Y
        # a size the user dragged to, kept within the current screen
        geo = self.monitor.get_geometry() if self.monitor else None
        width = self.settings.get('width')
        if width > 0:
            self.box_width = min(width, geo.width) if geo else width
        self.rows = max(1, min(self.rows, self.max_rows()))
        self.relayout()
        x, y = self.settings.get('x'), self.settings.get('y')
        self.place(x if x >= 0 else None, y if y >= 0 else None)
        self.render()

    def relayout(self):
        """Wrap the text to the box width and fix the caption at exactly self.rows rows."""
        self.layout = self.label.create_pango_layout('')
        self.layout.set_width((self.box_width - 2 * PAD_X) * Pango.SCALE)
        self.layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        self.label.set_size_request(self.box_width, self.caption_height())
        self.status_label.set_size_request(self.box_width, -1)

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
        text = self.text or self.status
        self.label.set_text(last_rows(self.layout, text, self.rows) if text else ' ')
        self.status_label.set_text(self.status)
        self.status_label.set_tooltip_text(self.status or None)
        self.status_label.set_visible(bool(self.text and self.status))
        # The optional status row participates in overlay clamping and pointer input.
        self.place(self.x, self.y)

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
        A normal window cannot move itself, so there only the right and bottom edges resize."""
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
            self.set_cursor(Gdk.Cursor.new_from_name(CURSORS.get(self.edges_at(x, y), 'grab'), None))

    def on_drag_begin(self, gesture, x, y):
        edges = self.edges_at(x, y)
        if edges:  # remember which edges, and where the box's edges were
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.resizing = (edges, self.x, self.y, self.x + self.box_width, self.y + self.caption_height())
            return
        self.set_cursor(Gdk.Cursor.new_from_name('grabbing', None))
        if self.layer:  # x, y are overlay (monitor) coordinates; remember where the box was grabbed
            self.dragging = (x - self.x, y - self.y)
            return
        surface = self.get_surface()  # the window manager moves normal windows
        if isinstance(surface, Gdk.Toplevel):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            surface.begin_move(gesture.get_device(), gesture.get_current_button(), x, y,
                               gesture.get_current_event_time())

    def on_drag_update(self, gesture, dx, dy):
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

    def on_drag_end(self, gesture, dx, dy):
        self.set_cursor(Gdk.Cursor.new_from_name('grab', None))
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

    def on_menu(self, gesture, n, x, y):
        if self.layer:  # let the menu take keyboard input while it is open
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)
        rect = Gdk.Rectangle()  # the menu belongs to the box, so use box coordinates
        bx, by = (self.x, self.y) if self.layer else (0, 0)
        rect.x, rect.y, rect.width, rect.height = int(x - bx), int(y - by), 1, 1
        self.popover.set_pointing_to(rect)
        if self.layer:  # let the compositor apply the keyboard mode first, or KWin closes the menu at once
            GLib.timeout_add(60, lambda: self.popover.popup() and False)
        else:
            self.popover.popup()


class NowCaption(Adw.Application):
    """Owns the window, the capture and the recognition worker. All methods run on the GTK
    thread; worker and capture callbacks are passed here with GLib.idle_add."""

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.settings = Settings()
        self.window = self.worker = None
        self.ready = self.closing = False
        self.source_gen = self.ui_gen = 0
        self.capture_failures = self.recognition_failures = 0
        self.retry_id = None
        self.recognition_retry_id = None
        self.recognition_retry_token = 0
        self.pending_saves = 0
        self.quit_requested = False
        self.capture = audio.Capture(
            on_audio=lambda gen, pcm: self.worker.feed(gen, pcm),
            on_started=lambda gen: GLib.idle_add(self.capture_started, gen),
            on_ended=lambda gen, reason: GLib.idle_add(self.capture_ended, gen, reason))
        self.connect('activate', self.on_activate)
        self.connect('shutdown', self.on_shutdown)
        self.add_actions()

    def build_menu(self, several_screens=False):
        """Compact menu: sound source, one-row size/background controls, transcript, app."""
        def section(*items):
            m = Gio.Menu()
            for text, action, icon in items:
                item = Gio.MenuItem.new(text, action)
                if icon:
                    item.set_attribute_value('verb-icon', GLib.Variant('s', icon))
                m.append_item(item)
            return Gio.MenuItem.new_section(None, m)

        menu = Gio.Menu()
        menu.append_item(section(('Desktop audio', 'app.source::desktop', None),
                                 ('Microphone', 'app.source::mic', None)))
        menu.append_item(section(('Filter out music', 'app.music-filter', None)))
        steppers = Gio.Menu()  # filled with button rows by CaptionWindow.update_menu
        for name in STEPPERS:
            item = Gio.MenuItem.new(None, None)
            item.set_attribute_value('custom', GLib.Variant('s', name))
            steppers.append_item(item)
        menu.append_section(None, steppers)
        place = [('Reset position', 'app.reset-position', None), ('Reset size', 'app.reset-size', None)]
        if several_screens:
            place.append(('Move to next screen', 'app.next-screen', None))
        menu.append_item(section(*place))
        menu.append_item(section(('Copy transcript', 'app.copy', None),
                                 ('Save transcript…', 'app.save', None),
                                 ('Clear transcript', 'app.clear', None)))
        menu.append_item(section(('About NowCaption', 'app.about', None),
                                 ('Quit', 'app.quit', None)))
        return menu

    def add_actions(self):
        def act(name, fn, accels=()):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', lambda *_: fn())
            self.add_action(a)
            if accels:
                self.accels['app.' + name] = list(accels)
        self.accels = {}  # registered once we know the window can take keyboard focus
        source = Gio.SimpleAction.new_stateful('source', GLib.VariantType.new('s'),
                                               GLib.Variant('s', self.settings.get('source')))
        source.connect('activate', self.on_source)
        self.add_action(source)
        music = Gio.SimpleAction.new_stateful('music-filter', None,
                                              GLib.Variant('b', bool(self.settings.get('music_filter'))))
        music.connect('activate', self.on_music_filter)
        self.add_action(music)
        act('larger', lambda: self.resize(+1), ['<Control>plus', '<Control>equal'])
        act('smaller', lambda: self.resize(-1), ['<Control>minus'])
        act('lighter', lambda: self.opacity(-0.15))
        act('darker', lambda: self.opacity(+0.15))
        act('reset-position', lambda: self.window.reset_position())
        act('reset-size', lambda: self.window.reset_size())
        act('next-screen', lambda: self.window.next_monitor())
        act('copy', self.copy_transcript)
        act('save', self.save_transcript, ['<Control>s'])
        act('clear', self.clear_transcript)
        act('about', self.about)
        act('quit', self.request_quit, ['<Control>q'])

    def on_source(self, action, value):
        if self.closing or self.quit_requested:
            return
        self.cancel_recognition_retry(reset=True)
        action.set_state(value)
        self.settings.set('source', value.get_string())
        if self.ready:
            self.start_capture()
        elif self.worker and not self.closing:
            # replaces the load error until the model loads or fails again
            self.window.show_failure('model', 'Retrying the speech model…')
            self.worker.restart()

    def on_music_filter(self, action, value):
        """Optional voice filter for songs and speech over music. Off by default: it uses more
        processor time and slightly lowers accuracy on plain speech."""
        on = not action.get_state().get_boolean()
        action.set_state(GLib.Variant('b', on))
        self.settings.set('music_filter', int(on))
        if self.worker:
            self.worker.set_music_filter(on)
        if on:
            self.window.show_status('Music filter on: voices are picked out from music.', seconds=4)
        else:
            self.window.show_status('Music filter off.', seconds=3)

    def resize(self, step):
        size = self.settings.get('size')
        i = min(range(len(SIZES)), key=lambda k: abs(SIZES[k] - size))
        self.settings.set('size', SIZES[max(0, min(len(SIZES) - 1, i + step))])
        self.window.restyle()

    def opacity(self, step):
        self.settings.set('opacity', round(max(0.2, min(1.0, self.settings.get('opacity') + step)), 2))
        self.window.restyle()

    def on_activate(self, app):
        if self.window:
            return self.window.present()
        self.window = CaptionWindow(self)
        self.window.connect('close-request', self.on_window_close)
        if not self.window.layer:  # an overlay never has keyboard focus, so shortcuts would never fire
            for action, keys in self.accels.items():
                self.set_accels_for_action(action, keys)
        self.window.present()
        self.window.show_status('Loading speech model…')
        # models load on the worker thread, so the window stays responsive meanwhile
        self.worker = engine.RecognitionWorker(lambda kind, value: GLib.idle_add(self.on_worker, kind, value))
        self.worker.start()
        if self.settings.get('music_filter'):
            self.worker.set_music_filter(True)
        GLib.timeout_add_seconds(60, self.release_memory)
        shot = os.environ.get('NOWCAPTION_SCREENSHOT')  # "file.png" or "file.png:seconds"
        if shot:
            path, _, after = shot.partition(':')
            GLib.timeout_add_seconds(int(after or 20), self.window.save_png, path)

    def release_memory(self):
        if self.closing:
            return False
        engine.release_free_memory()
        return True

    def on_shutdown(self, app):
        self.closing = True
        self.cancel_recognition_retry()
        if self.retry_id:
            GLib.source_remove(self.retry_id)
            self.retry_id = None
        self.capture.stop()
        if self.worker:
            self.worker.stop()

    def request_quit(self):
        """Keep accepted saves alive until their snapshot and async write finish."""
        if self.pending_saves:
            self.quit_requested = True
            if self.window:
                self.window.show_status('Finishing transcript save before quitting…', sticky=True)
            return
        self.quit()

    def on_window_close(self, window):
        self.request_quit()
        return True

    def cancel_recognition_retry(self, reset=False):
        self.recognition_retry_token += 1
        if self.recognition_retry_id:
            GLib.source_remove(self.recognition_retry_id)
            self.recognition_retry_id = None
        if reset:
            self.recognition_failures = 0

    def retry_recognition(self, token, source_gen):
        if token != self.recognition_retry_token:
            return False
        self.recognition_retry_id = None
        self.recognition_retry_token += 1
        if self.worker and not self.closing and source_gen == self.source_gen:
            self.worker.restart(recover_only=True, source_gen=source_gen)
        return False

    # --- recognition worker events ----------------------------------------------------------

    def on_worker(self, kind, value):
        if self.closing:
            return False
        if kind == 'ready':
            self.ready = True
            self.window.clear_failure('model')
            self.start_capture()
        elif kind == 'caption':
            gen, text = value
            if gen == self.ui_gen:  # ignore captions from before the last Clear
                self.window.show_text(text)
        elif kind == 'recognition-ok':
            if value == self.source_gen:
                self.cancel_recognition_retry(reset=True)
                self.window.clear_failure('recognition')
        elif kind == 'snapshot':
            fn, text = value
            fn(text)
        elif kind == 'snapshot-error':
            fn, message = value
            if fn:
                fn(message)
            else:
                self.window.show_status(f'Could not prepare the transcript: {message}', seconds=8)
        elif kind == 'warning':
            self.window.show_status(value, seconds=6)
        elif kind == 'music-filter':
            action = self.lookup_action('music-filter')
            if action and action.get_state().get_boolean() != value:  # it could not be loaded
                action.set_state(GLib.Variant('b', value))
                self.settings.set('music_filter', int(value))
        elif kind == 'overload':
            self.window.show_status(f'Falling behind: skipped {value:.1f} s of audio', seconds=4)
        elif kind == 'load-error':
            self.ready = False
            self.window.show_failure('model', f'{value} Right-click and choose a sound source to try again.')
        elif kind == 'error':
            if not getattr(self.worker, 'broken', True):
                return False  # a source change already recovered this queued error
            self.cancel_recognition_retry()
            self.recognition_failures += 1
            if self.recognition_failures <= 3:
                delay = 2 ** self.recognition_failures
                self.window.show_failure('recognition', f'{value}. Restarting in {delay} s…')
                self.recognition_retry_id = GLib.timeout_add_seconds(
                    delay, self.retry_recognition, self.recognition_retry_token, self.source_gen)
            else:
                self.window.show_failure('recognition', f'{value}. Right-click and choose a sound source to try again.')
        return False

    # --- capture ----------------------------------------------------------------------------

    def start_capture(self):
        if not self.ready or self.closing:
            return
        self.cancel_recognition_retry()
        if self.retry_id:
            GLib.source_remove(self.retry_id)
            self.retry_id = None
        mic = self.settings.get('source') == 'mic'
        self.source_gen += 1
        self.worker.set_source(self.source_gen)
        # NOWCAPTION_SOURCE names any PulseAudio/PipeWire source, overriding the menu choice
        device = os.environ.get('NOWCAPTION_SOURCE') or (audio.MICROPHONE if mic else audio.DESKTOP)
        try:
            self.capture.start(device, self.source_gen)
        except Exception as e:
            return self.capture_ended(self.source_gen, str(e))
        if not self.window.text:
            self.window.show_status('Listening to the microphone…' if mic else 'Listening to desktop audio…')

    def capture_started(self, gen):
        if gen == self.source_gen and self.capture_failures:
            self.capture_failures = 0
            self.window.clear_failure('capture')
            self.window.show_status('Audio is back.', seconds=3)
        return False

    def capture_ended(self, gen, reason):
        """Capture stopped on its own (device gone, sound server restarted…): retry with backoff."""
        if gen != self.source_gen or self.closing:
            return False
        delay = min(30, 2 ** self.capture_failures)
        self.capture_failures += 1
        LOG.warning('Audio capture stopped (%s); retrying in %d s', reason, delay)
        self.window.show_failure('capture', f'Audio capture stopped ({reason}). Retrying in {delay} s…')
        self.retry_id = GLib.timeout_add_seconds(delay, self.retry_capture)
        return False

    def retry_capture(self):
        self.retry_id = None
        self.start_capture()
        return False

    # --- transcript -------------------------------------------------------------------------

    def copy_transcript(self):
        def copy(text):
            self.window.get_clipboard().set(text)
            self.window.show_status('Transcript copied.' if text else 'Nothing to copy yet.', seconds=3)
        if self.worker:
            try:
                self.worker.snapshot(copy)
            except Exception as e:
                self.window.show_status(f'Could not copy the transcript: {e}', seconds=8)

    def clear_transcript(self):
        if self.worker and not self.closing and not self.quit_requested:
            self.cancel_recognition_retry(reset=True)
            self.ui_gen += 1  # captions already on their way from before the clear are dropped
            self.worker.clear(self.ui_gen)
            self.window.show_text('')
            # Discard pre-Clear audio buffered in GStreamer as well as in recognition.
            if self.ready:
                self.start_capture()

    def save_transcript(self):
        if not self.worker or self.closing or self.quit_requested:
            return
        dialog = Gtk.FileDialog(title='Save transcript', initial_name=GLib.DateTime.new_now_local().format(
            'Captions %Y-%m-%d %H.%M.txt'))

        def chosen(d, result):
            try:
                f = d.save_finish(result)
            except GLib.Error as e:
                if not e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self.window.show_status(f'Could not save the transcript: {e.message}', seconds=8)
                return
            if self.closing or self.quit_requested:
                return
            self.begin_save()

            def failed_snapshot(error):
                self.window.show_status(f'Could not save the transcript: {error}', seconds=8)
                self.finish_save(False)

            try:
                self.worker.snapshot(lambda text: self.write_transcript(f, text, tracked=True),
                                     on_error=failed_snapshot)
            except Exception as e:
                failed_snapshot(e)

        dialog.save(None, None, chosen)  # its own window: the caption overlay is too small to host it

    def begin_save(self):
        self.pending_saves += 1
        self.hold()

    def finish_save(self, success):
        self.pending_saves -= 1
        if not success:
            # A failed save needs a visible error and an opportunity to retry.
            self.quit_requested = False
        should_quit = self.quit_requested and not self.pending_saves
        self.release()
        if should_quit:
            self.quit()

    def write_transcript(self, f, text, tracked=False):
        """Write asynchronously (a slow or network destination never stalls the window) and say
        whether it worked."""
        if not tracked:
            self.begin_save()

        def failed(error):
            LOG.warning('Saving the transcript failed: %s', error)
            self.window.show_status(f'Could not save the transcript: {error}', seconds=8)
            self.finish_save(False)

        def written(f, result):
            try:
                f.replace_contents_finish(result)
            except GLib.Error as e:
                failed(e.message)
                return
            self.window.show_status(f'Transcript saved to {f.get_basename()}.', seconds=4)
            self.finish_save(True)
        try:
            f.replace_contents_bytes_async(GLib.Bytes.new((text + '\n').encode()), None, False,
                                           Gio.FileCreateFlags.REPLACE_DESTINATION, None, written)
        except Exception as e:
            failed(e)

    def about(self):
        about = Adw.AboutDialog(
            application_name='NowCaption', application_icon=APP_ID, version=__version__,
            developer_name='Techlm77', license_type=Gtk.License.MIT_X11,
            website='https://github.com/Techlm77/NowCaption',
            issue_url='https://github.com/Techlm77/NowCaption/issues',
            comments='Live captions for anything your computer plays, or your microphone. '
                     'Speech is recognised on this computer; audio never leaves it.')
        about.add_legal_section('Speech model', '© NVIDIA (stt_en_fastconformer_hybrid_large_streaming_multi)',
                                Gtk.License.CUSTOM, 'Creative Commons Attribution 4.0 International (CC-BY-4.0). '
                                'Exported to ONNX and quantized to int8 for NowCaption. '
                                'https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi')
        about.add_legal_section('Punctuation model', 'Edge-Punct-Casing by frankyoujian', Gtk.License.APACHE_2_0, None)
        about.add_legal_section('Music filter model', 'DPDFNet2 © Ceva', Gtk.License.APACHE_2_0, None)
        about.add_legal_section('sherpa-onnx', '© k2-fsa', Gtk.License.APACHE_2_0, None)
        about.present(None)  # its own window, not squeezed into the caption box


def main():
    # problems are logged with details to stderr (the terminal, or the journal for the menu entry)
    logging.basicConfig(level=os.environ.get('NOWCAPTION_LOG', 'WARNING').upper(),
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    gtk, adw = (Gtk.get_major_version(), Gtk.get_minor_version()), (Adw.get_major_version(), Adw.get_minor_version())
    if gtk < (4, 12) or adw < (1, 5):
        print(f'NowCaption needs GTK 4.12 or newer and libadwaita 1.5 or newer; this system has '
              f'GTK {gtk[0]}.{gtk[1]} and libadwaita {adw[0]}.{adw[1]}. The Flatpak includes both.',
              file=sys.stderr)
        return 1
    return NowCaption().run(sys.argv)
