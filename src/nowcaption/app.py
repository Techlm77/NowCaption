"""NowCaption: on-device live captions for anything your computer plays, or your microphone."""
import difflib
import logging
import math
import os
import re
import sys
import time
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
import cairo  # noqa: E402
gi.require_version('Pango', '1.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

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
    'text-size': ('Text size', ('Smaller text', 'app.smaller', 'list-remove-symbolic'),
                  ('Larger text', 'app.larger', 'list-add-symbolic')),
    'background': ('Background', ('More see-through', 'app.lighter', 'list-remove-symbolic'),
                   ('More solid', 'app.darker', 'list-add-symbolic')),
}
MENU_INDENT = 34  # button rows start where the menu's item labels do, after the tick column
# Caption text colours, as TV captions offer them. Their swatches in the menu are buttons too,
# so colours can be compared on the captions before the menu closes.
COLOURS = {'white': ('White', '#ffffff'), 'yellow': ('Yellow', '#ffff00'),
           'lime': ('Lime', '#00ff00'), 'cyan': ('Cyan', '#00ffff'),
           'orange': ('Orange', '#ff9900'), 'pink': ('Pink', '#ff7ad9'),
           'lavender': ('Lavender', '#c8a2ff'), 'brown': ('Brown', '#d2a26e'),
           'red': ('Red', '#ff5050')}
# Animations, in ms. All are instant when the desktop turns animations off. The caption box
# fades in when NowCaption starts and out when it quits; the rows glide up a row instead of
# jumping; new words fade in; text colour and background blend into a new choice; status
# messages fade, and a message that waits for speech ("Listening…") breathes until it comes.
FADE_IN_MS, FADE_OUT_MS = 200, 150
GLIDE_MS, WORD_MS, STATUS_MS, CHANGE_MS, BREATH_MS = 200, 120, 150, 150, 2000
BREATH_STEP_MS = 40  # breathing is slow and may last long: 25 steps a second, not every frame
DEFAULTS = {'source': 'desktop', 'size': 30, 'opacity': 0.78, 'x': -1, 'y': -1, 'monitor': '',
            'width': -1, 'rows': ROWS, 'colour': 'white', 'history': True}
LIMITS = {'size': (SIZES[0], SIZES[-1]), 'opacity': (0.2, 1.0), 'rows': (1, 100)}
CHOICES = {'source': ('desktop', 'mic'), 'colour': tuple(COLOURS)}


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
            if isinstance(default, bool):
                value = self.kf.get_boolean('main', key)
            elif isinstance(default, int):
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
        {bool: self.kf.set_boolean, int: self.kf.set_integer,
         float: self.kf.set_double}.get(type(DEFAULTS[key]), self.kf.set_string)('main', key, value)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.kf.save_to_file(str(self.path))
        except (OSError, GLib.Error) as e:
            LOG.warning('Could not save settings to %s: %s', self.path, e)


def wrapped_lines(layout, text):
    """text in rows, as Pango wraps it in the caption box."""
    layout.set_text(text, -1)
    data = text.encode()
    return [data[l.start_index:l.start_index + l.length].decode(errors='ignore').strip()
            for l in layout.get_lines_readonly()]


def bare(word):
    """A word without the punctuation and capital the punctuation model may still change."""
    return word.strip('.,?!;:"\'').lower()


def ease_out(p):
    return 1 - (1 - p) ** 3


class CaptionText(Gtk.Widget):
    """The caption rows: a Gtk.Label, which screen readers read, clipped to exactly the rows.
    When the passage moves up, the rows glide up instead of jumping (the label then shows the
    rows on their way out too, moved up by self.offset). New words fade in. A status message
    shown in place of captions fades in, and breathes while it waits for speech. Without
    animations, the label shows just the last rows."""

    def __init__(self, label, **kwargs):
        super().__init__(overflow=Gtk.Overflow.HIDDEN, **kwargs)
        self.label = label
        label.set_parent(self)
        self.layout, self.rows, self.row_height = None, ROWS, 0
        self.text, self.lines, self.words, self.status = '', [], [], False
        self.glide, self.glide_start = 0.0, 0  # rows to glide up, counted from glide_start (µs)
        self.appeared = {}  # passage word index: when it appeared (µs), while it fades in
        self.shown_at = self.breath_start = None  # status message: when it appeared, began breathing
        self.offset, self.tick, self.breath_timer, self.attributed = 0.0, 0, 0, False

    def do_dispose(self):
        self.stop()
        if self.label.get_parent() is self:
            self.label.unparent()
        Gtk.Widget.do_dispose(self)

    def do_map(self):
        Gtk.Widget.do_map(self)
        self.start()

    def do_unmap(self):
        self.stop()
        Gtk.Widget.do_unmap(self)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            height = self.rows * self.row_height
            return height, height, -1, -1
        return 0, self.label.measure(orientation, -1)[1], -1, -1

    def do_size_allocate(self, width, height, baseline):
        width = max(width, self.label.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        height = max(height, self.label.measure(Gtk.Orientation.VERTICAL, width)[0])
        up = Gsk.Transform().translate(Graphene.Point().init(0, -self.offset)) if self.offset else None
        self.label.allocate(width, height, -1, up)

    def configure(self, layout, rows, row_height):
        """A new width, font or number of rows: the rows are shown at rest."""
        self.layout, self.rows, self.row_height = layout, rows, row_height
        self.glide, self.appeared = 0.0, {}
        self.queue_resize()

    def show(self, text, status=False, waiting=False):
        """Show the passage, or with status, a status message (waiting: one that waits for
        speech, so it breathes)."""
        now = GLib.get_monotonic_time()
        lines = wrapped_lines(self.layout, text) if text else []
        words = text.split()
        if self.get_mapped() and Adw.get_enable_animations(self):
            moved = self.moved(lines)
            if moved is None or moved > self.rows:
                self.glide = 0.0
            elif moved:
                self.glide, self.glide_start = self.gliding(now) + moved, now
            self.appeared = {} if status else self.carry(words, now, fresh=self.status or not self.words)
            if not (status and text):
                self.shown_at = None
            elif not self.status or text != self.text:
                self.shown_at = now
            self.breath_start = (self.breath_start or now) if waiting else None
        else:
            self.glide, self.appeared, self.shown_at, self.breath_start = 0.0, {}, None, None
        self.text, self.lines, self.words, self.status = text, lines, words, status
        self.start(now)

    def moved(self, lines):
        """How many rows the passage moved up, judged by the row that was on top: None when
        that row is gone (the rows wrapped differently, or the passage changed)."""
        if not self.lines or not lines or self.status:
            return 0
        line = lambda row: ' '.join(bare(w) for w in row.split())
        top = line(self.lines[max(0, len(self.lines) - self.rows)])
        rest = max(0, len(lines) - self.rows)  # the top row once the passage is at rest
        for moved in range(min(rest, self.rows) + 1):
            if line(lines[rest - moved]) == top:
                return moved
        return None

    def carry(self, words, now, fresh):
        """When each of the passage's words appeared: words already shown keep their time,
        even with new punctuation, and words added appear now. fresh: no captions were shown
        before (a status message, or an empty box), so every word is new."""
        if fresh:
            return {i: now for i in range(len(words))}
        a, b = max(0, len(self.words) - 80), max(0, len(words) - 80)  # only the end changes
        old, new = [bare(w) for w in self.words[a:]], [bare(w) for w in words[b:]]
        appeared = {}
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            same = 0 if tag in ('insert', 'delete') else min(i2 - i1, j2 - j1)  # kept, or reworded
            for d in range(same):
                if a + i1 + d in self.appeared:
                    appeared[b + j1 + d] = self.appeared[a + i1 + d]
            for j in range(j1 + same, j2):
                appeared[b + j] = now
        return appeared

    def gliding(self, now):
        """Rows still to glide up at time now (µs)."""
        if not self.glide:
            return 0.0
        p = (now - self.glide_start) / (GLIDE_MS * 1000)
        if p >= 1:
            self.glide = 0.0
            return 0.0
        return self.glide * (1 - ease_out(max(0.0, p)))

    def update(self, now):
        """Show the rows, the words fading in and the status message as of now (µs). True
        while the rows, words or a status message's fade still move (breathing is timed by
        breathe instead)."""
        glide = self.gliding(now)
        extra = min(math.ceil(glide), max(0, len(self.lines) - self.rows))
        lines = self.lines[-(self.rows + extra):]
        text = '\n'.join(lines) if lines else ' '
        if self.label.get_text() != text:
            self.label.set_text(text)
        offset = (extra - min(glide, extra)) * self.row_height
        if offset != self.offset:
            self.offset = offset
            self.queue_allocate()
        self.appeared = {i: t for i, t in self.appeared.items() if now - t < WORD_MS * 1000}
        if self.appeared or self.attributed:
            self.label.set_attributes(self.fading(text, now) if self.appeared else None)
            self.attributed = bool(self.appeared)
        opacity = 1.0
        if self.shown_at is not None:
            p = (now - self.shown_at) / (STATUS_MS * 1000)
            if p >= 1:
                self.shown_at = None
            else:
                opacity = ease_out(max(0.0, p))
        if self.breath_start is not None:  # 1, down to 0.55, and back every BREATH_MS
            opacity *= 1 - 0.225 * (1 - math.cos(2 * math.pi * (now - self.breath_start) / (BREATH_MS * 1000)))
        self.label.set_opacity(opacity)
        return bool(self.glide or self.appeared or self.shown_at is not None)

    def fading(self, text, now):
        """Pango attributes that fade in the words that just appeared, in the shown text."""
        spans = [m.span() for m in re.finditer(r'\S+', text)]
        first = len(self.words) - len(spans)  # the passage index of the first word shown
        attrs = Pango.AttrList()
        for i, t in self.appeared.items():
            if 0 <= i - first < len(spans):
                start, end = spans[i - first]
                p = min(1.0, max(0.0, (now - t) / (WORD_MS * 1000)))
                alpha = Pango.attr_foreground_alpha_new(max(1, int(65535 * p)))  # 0 means opaque
                alpha.start_index, alpha.end_index = len(text[:start].encode()), len(text[:end].encode())
                attrs.insert(alpha)
        return attrs

    def start(self, now=None):
        """Show the current state, and keep updating it while anything moves."""
        moving = self.update(GLib.get_monotonic_time() if now is None else now)
        if not self.get_mapped():
            return
        if moving and not self.tick:
            self.tick = self.add_tick_callback(self.on_tick)
        if self.breath_start is not None and not self.breath_timer:
            self.breath_timer = GLib.timeout_add(BREATH_STEP_MS, self.breathe)

    def stop(self):
        if self.tick:
            self.remove_tick_callback(self.tick)
            self.tick = 0
        if self.breath_timer:
            GLib.source_remove(self.breath_timer)
            self.breath_timer = 0

    def breathe(self):
        if self.breath_start is None:
            self.breath_timer = 0
            return GLib.SOURCE_REMOVE
        if not self.tick:  # otherwise the frames update it anyway
            self.update(GLib.get_monotonic_time())
        return GLib.SOURCE_CONTINUE

    def on_tick(self, widget, clock):
        if self.update(clock.get_frame_time()):
            return GLib.SOURCE_CONTINUE
        self.tick = 0
        return GLib.SOURCE_REMOVE


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
        self.caption = CaptionText(self.label, margin_start=PAD_X, margin_end=PAD_X,
                                   margin_top=PAD_Y, margin_bottom=PAD_Y)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.add_css_class('caption-box')
        self.box.append(self.caption)
        self.status_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                      single_line_mode=True, accessible_role=Gtk.AccessibleRole.STATUS)
        self.status_label.add_css_class('caption-status')
        self.status_label.set_visible(False)
        self.box.append(self.status_label)
        self.footer_shown = self.footer_hiding = False
        self.fades, self.faded_in = {}, False
        self.box.connect('map', self.fade_in)
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
        """Set the menu, with the text size, background and text colour rows as buttons that
        keep it open. The size and background rows show their value between their buttons; the
        colour row shows the chosen colour's name, with a line of colour dots under it."""
        self.popover.set_menu_model(self.app.build_menu(several_screens))
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
        self.show_values()

    def show_values(self):
        values = getattr(self, 'values', None)
        if values:
            values['text-size'].set_label(f"{self.settings.get('size')} px")
            values['background'].set_label(f"{round(self.settings.get('opacity') * 100)}%")
            values['text-colour'].set_label(COLOURS[self.settings.get('colour')][0])

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
        colour = COLOURS[self.settings.get('colour')][1]
        swatches = ''.join(f'window.nowcaption .colour-{name} {{ background: {rgb}; }}\n'
                           for name, (_, rgb) in COLOURS.items())
        self.css.load_from_string(f"""
            window.nowcaption {{ background: transparent; }}
            window.nowcaption .caption-box {{ background: rgba(0, 0, 0, {alpha}); border-radius: 14px;
                                             transition: background-color {CHANGE_MS}ms ease-out; }}
            window.nowcaption .caption-text {{ color: {colour}; font-size: {px}px; font-weight: 500;
                                              transition: color {CHANGE_MS}ms ease-out; }}
            window.nowcaption .caption-status {{ color: #cccccc; font-size: {max(14, round(px * 0.55))}px;
                                                padding: 0 {PAD_X}px {PAD_Y}px; }}
            window.nowcaption .colour-swatch {{ min-width: 18px; min-height: 18px; border-radius: 99px;
                                               border: 1px solid rgba(0, 0, 0, 0.4); }}
            window.nowcaption button.colour-dot {{ padding: 3px; min-width: 0; min-height: 0;
                                                  border-radius: 99px; }}
            window.nowcaption button.colour-dot:checked {{ background: none; }}
            window.nowcaption button.colour-dot:checked .colour-swatch {{
                box-shadow: 0 0 0 2px @popover_bg_color, 0 0 0 3px currentColor; }}
            {swatches}""")
        self.show_values()
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
        self.caption.set_size_request(self.box_width - 2 * PAD_X, -1)
        self.caption.configure(self.layout, self.rows, self.row_height)
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


def session_heading(started):
    """A session's heading in the transcript, such as "Thursday 23 October 2026, 14:05"."""
    return time.strftime('%A %-d %B %Y, %H:%M', time.localtime(started))


def rgba(spec):
    colour = Gdk.RGBA()
    colour.parse(spec)
    return colour


TIME = re.compile(r'(\[\d\d:\d\d:\d\d\]) ')


class TranscriptWindow(Adw.Window):
    """Every kept session, oldest first, then this one as it grows: scroll, select and copy any
    of it, find words (Ctrl+F), save it, or delete the history. Earlier sessions come from the
    history folder; this one is fetched again from the recognition worker shortly after each
    caption, and only its changed lines are replaced, so a selection or the place being read
    stays put. The view follows new lines only while it is at the end."""

    REFRESH_MS = 500

    def __init__(self, app):
        super().__init__(application=app, default_width=680, default_height=720)
        self.app, self.closed = app, False
        self.block, self.current = [], None  # this session's lines as shown (heading first), and its start
        self.matches, self.match, self.refresh_id = [], -1, None

        self.buffer = Gtk.TextBuffer()
        self.time_tag = self.buffer.create_tag('time')
        self.heading_tag = self.buffer.create_tag('heading', weight=Pango.Weight.BOLD,
                                                  pixels_above_lines=18, pixels_below_lines=6)
        self.match_tag = self.buffer.create_tag('match', background_rgba=rgba('rgba(246, 211, 45, 0.45)'))
        self.current_tag = self.buffer.create_tag('current-match', background_rgba=rgba('rgba(255, 140, 0, 0.75)'))
        self.end = self.buffer.create_mark('end', self.buffer.get_end_iter(), False)
        self.view = Gtk.TextView(buffer=self.buffer, editable=False, cursor_visible=False,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=18, right_margin=18,
                                 top_margin=6, bottom_margin=18)
        self.scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True)
        self.stack = Gtk.Stack()
        self.stack.add_named(Adw.StatusPage(icon_name='format-justify-left-symbolic', title='No captions yet',
                                            description='Each line appears here once it is finished.'), 'empty')
        self.stack.add_named(self.scroller, 'text')
        self.toasts = Adw.ToastOverlay(child=self.stack)

        self.entry = Gtk.SearchEntry(placeholder_text='Find in transcript', hexpand=True)
        self.count = Gtk.Label(css_classes=['dim-label', 'numeric'])
        bar = Gtk.Box(spacing=6)
        bar.append(self.entry)
        bar.append(self.count)
        for icon, tooltip, step in (('go-up-symbolic', 'Previous match', -1), ('go-down-symbolic', 'Next match', 1)):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
            button.connect('clicked', lambda b, step=step: self.step(step))
            bar.append(button)
        self.search = Gtk.SearchBar(child=Adw.Clamp(child=bar, maximum_size=560), show_close_button=True)
        self.search.connect_entry(self.entry)
        self.search.set_key_capture_widget(self)  # typing anywhere starts a search
        self.entry.connect('search-changed', lambda e: self.find())
        self.entry.connect('activate', lambda e: self.step(1))
        self.entry.connect('next-match', lambda e: self.step(1))
        self.entry.connect('previous-match', lambda e: self.step(-1))

        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title='Transcript')
        header.set_title_widget(self.title)
        find = Gtk.ToggleButton(icon_name='edit-find-symbolic', tooltip_text='Find (Ctrl+F)')
        find.update_property([Gtk.AccessibleProperty.LABEL], ['Find'])
        find.bind_property('active', self.search, 'search-mode-enabled',
                           GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(find)
        for icon, tooltip, action in (('user-trash-symbolic', 'Delete history…', self.confirm_delete),
                                      ('document-save-symbolic', 'Save…', app.save_transcript),
                                      ('edit-copy-symbolic', 'Copy all', self.copy_all)):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
            button.connect('clicked', lambda b, action=action: action())
            header.pack_end(button)
        page = Adw.ToolbarView(content=self.toasts)
        page.add_top_bar(header)
        page.add_top_bar(self.search)
        self.set_content(page)

        keys = Gtk.ShortcutController()
        for trigger, action in (('<Control>f', lambda: self.search.set_search_mode(True)), ('<Control>w', self.close)):
            keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trigger),
                                           action=Gtk.CallbackAction.new(lambda w, a, action=action: action() or True)))
        self.add_controller(keys)
        Adw.StyleManager.get_default().connect('notify::dark', lambda *a: GLib.idle_add(self.restyle))
        self.restyle()
        self.reload()

    def restyle(self):
        colour = self.view.get_color()  # the text colour, dimmed for the times
        colour.alpha *= 0.55
        self.time_tag.set_property('foreground-rgba', colour)
        return False

    def show_title(self):
        keep = self.app.settings.get('history')
        self.title.set_subtitle(f'Last {engine.HISTORY_DAYS} days' if keep else 'This session only')

    def reload(self):
        """Read the kept sessions again and fetch this one."""
        engine.prune_history()
        if self.current:
            self.buffer.delete_mark(self.current)
        self.buffer.set_text('')
        for started, text in self.app.earlier_sessions():
            self.add_lines([session_heading(started)] + text.splitlines())
        self.current = self.buffer.create_mark(None, self.buffer.get_end_iter(), True)  # stays before this session
        self.block = []
        self.show_title()
        self.show_page()
        self.refresh()
        GLib.idle_add(self.scroll_to_end)

    def add_lines(self, lines, heading=True):
        """Append lines; the first is a session's heading unless heading is False."""
        for i, line in enumerate(lines):
            if i == 0 and heading:
                self.buffer.insert_with_tags(self.buffer.get_end_iter(), line + '\n', self.heading_tag)
                continue
            stamp = TIME.match(line)
            if stamp:
                self.buffer.insert_with_tags(self.buffer.get_end_iter(), stamp.group(1), self.time_tag)
                line = ' ' + line[stamp.end():]
            self.buffer.insert(self.buffer.get_end_iter(), line + '\n')

    def changed(self):
        """A caption changed: fetch this session again soon (at most twice a second)."""
        if self.refresh_id is None:
            self.refresh_id = GLib.timeout_add(self.REFRESH_MS, self.refresh)

    def refresh(self):
        self.refresh_id = None
        if self.app.worker and not self.app.closing and not self.closed:
            try:
                self.app.worker.snapshot(self.apply)
            except RuntimeError as e:
                LOG.warning('Could not refresh the transcript: %s', e)
        return False

    def apply(self, text):
        """Show this session's transcript, replacing only the lines that changed."""
        if self.closed:
            return
        lines = text.splitlines()
        block = [session_heading(self.app.started)] + lines if lines else []
        same = 0
        while same < min(len(block), len(self.block)) and block[same] == self.block[same]:
            same += 1
        if same == len(block) == len(self.block):
            return
        follow = self.at_end()
        start = self.buffer.get_iter_at_mark(self.current).get_offset()
        start += sum(len(line) + 1 for line in self.block[:same])
        self.buffer.delete(self.buffer.get_iter_at_offset(start), self.buffer.get_end_iter())
        self.add_lines(block[same:], heading=same == 0)
        self.block = block
        self.show_page()
        if self.entry.get_text():
            self.find(keep=True)
        if follow:
            GLib.idle_add(self.scroll_to_end)

    def at_end(self):
        adjustment = self.scroller.get_vadjustment()
        return adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 24

    def scroll_to_end(self):
        self.view.scroll_to_mark(self.end, 0, False, 0, 1)
        return False

    def show_page(self):
        self.stack.set_visible_child_name('text' if self.buffer.get_char_count() else 'empty')

    def find(self, keep=False):
        """Highlight every match of the search text and go to the newest (or keep the place)."""
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(self.match_tag, start, end)
        self.buffer.remove_tag(self.current_tag, start, end)
        for a, b in self.matches:
            self.buffer.delete_mark(a)
            self.buffer.delete_mark(b)
        self.matches = []
        text, where = self.entry.get_text(), start
        while text:
            found = where.forward_search(text, Gtk.TextSearchFlags.CASE_INSENSITIVE, None)
            if found and len(found) == 3:  # (found, start, end) in older PyGObject
                found = found[1:] if found[0] else None
            if not found:
                break
            a, b = found
            self.buffer.apply_tag(self.match_tag, a, b)
            self.matches.append((self.buffer.create_mark(None, a, True), self.buffer.create_mark(None, b, False)))
            where = b
        if keep and 0 <= self.match < len(self.matches):
            self.show_match(scroll=False)
        else:
            self.match = len(self.matches) - 1
            self.show_match(scroll=True)

    def step(self, direction):
        if self.matches:
            self.buffer.remove_tag(self.current_tag, *self.buffer.get_bounds())
            self.match = (self.match + direction) % len(self.matches)
            self.show_match(scroll=True)

    def show_match(self, scroll):
        if not self.entry.get_text():
            self.count.set_label('')
        elif not self.matches:
            self.count.set_label('No matches')
        else:
            a, b = self.matches[self.match]
            self.buffer.apply_tag(self.current_tag, self.buffer.get_iter_at_mark(a), self.buffer.get_iter_at_mark(b))
            self.count.set_label(f'{self.match + 1} of {len(self.matches)}')
            if scroll:
                self.view.scroll_to_mark(a, 0.1, True, 0, 0.3)

    def copy_all(self):
        self.app.copy_transcript()
        self.toasts.add_toast(Adw.Toast(title='Transcript copied', timeout=2))

    def confirm_delete(self):
        dialog = Adw.AlertDialog(
            heading='Delete all transcripts?',
            body=f'This deletes everything NowCaption has kept from the last {engine.HISTORY_DAYS} '
                 'days, including this session, and clears the captions on screen.')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('delete', 'Delete')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda d, response: response == 'delete' and self.app.delete_history())
        dialog.present(self)


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
        self.started = time.time()
        self.history_path = engine.history_file(self.started)  # created once something is said
        self.transcript = None  # the transcript window, while it is open
        self.save_queues = {}  # destination URI -> accepted writes, in snapshot order
        self.quit_requested = False
        self.fading_out = False
        self.capture = audio.Capture(
            on_audio=lambda gen, pcm: self.worker.feed(gen, pcm),
            on_started=lambda gen: GLib.idle_add(self.capture_started, gen),
            on_ended=lambda gen, reason: GLib.idle_add(self.capture_ended, gen, reason))
        self.connect('activate', self.on_activate)
        self.connect('shutdown', self.on_shutdown)
        self.add_actions()

    def build_menu(self, several_screens=False):
        """The caption box's menu, in labelled sections: what to listen to, how captions look,
        the transcript, the box's place, and the app. CaptionWindow.update_menu fills the
        'custom' items with rows of buttons that keep the menu open."""
        def section(label, *items):
            m = Gio.Menu()
            for item in items:
                m.append_item(item)
            return Gio.MenuItem.new_section(label, m)

        def custom(name):
            item = Gio.MenuItem.new(None, None)
            item.set_attribute_value('custom', GLib.Variant('s', name))
            return item

        item = Gio.MenuItem.new
        menu = Gio.Menu()
        menu.append_item(section('Listen to', item('Desktop audio', 'app.source::desktop'),
                                 item('Microphone', 'app.source::mic')))
        menu.append_item(section('Appearance', *(custom(name) for name in (*STEPPERS, 'text-colour'))))
        menu.append_item(section('Transcript', item('Open transcript', 'app.transcript'),
                                 item(f'Keep {engine.HISTORY_DAYS} days of history', 'app.history'),
                                 item('Clear this session', 'app.clear')))
        place = [item('Reset position and size', 'app.reset')]
        if several_screens:
            place.append(item('Move to next screen', 'app.next-screen'))
        menu.append_item(section(None, *place))
        menu.append_item(section(None, item('About NowCaption', 'app.about'), item('Quit', 'app.quit')))
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
        colour = Gio.SimpleAction.new_stateful('colour', GLib.VariantType.new('s'),
                                               GLib.Variant('s', self.settings.get('colour')))
        colour.connect('activate', self.on_colour)
        self.add_action(colour)
        act('larger', lambda: self.resize(+1), ['<Control>plus', '<Control>equal'])
        act('smaller', lambda: self.resize(-1), ['<Control>minus'])
        act('lighter', lambda: self.opacity(-0.15))
        act('darker', lambda: self.opacity(+0.15))
        history = Gio.SimpleAction.new_stateful('history', None, GLib.Variant('b', self.settings.get('history')))
        history.connect('activate', self.on_history)
        self.add_action(history)
        act('transcript', self.open_transcript, ['<Control>t'])
        act('reset', lambda: (self.window.reset_position(), self.window.reset_size()))
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

    def on_history(self, action, value):
        keep = not action.get_state().get_boolean()
        action.set_state(GLib.Variant('b', keep))
        self.settings.set('history', keep)
        if self.worker:
            self.worker.set_history(self.history_path if keep else None)
        if self.transcript:
            self.transcript.show_title()

    def open_transcript(self):
        if self.transcript is None:
            self.transcript = TranscriptWindow(self)
            self.transcript.connect('close-request', self.on_transcript_closed)
        self.transcript.present()

    def on_transcript_closed(self, window):
        window.closed = True
        self.transcript = None
        return False

    def earlier_sessions(self):
        """Kept sessions before this one, oldest first, as (start time, text)."""
        sessions = []
        for started, path in engine.history_files():
            if path == self.history_path:
                continue
            try:
                text = path.read_text(errors='replace').strip()
            except OSError as e:
                LOG.warning('Could not read the transcript %s: %s', path, e)
                continue
            if text:
                sessions.append((started, text))
        return sessions

    def full_transcript(self, current):
        """Every kept session and this one, each under its heading, as Copy and Save give it."""
        parts = [f'{session_heading(started)}\n{text}' for started, text in self.earlier_sessions()]
        if current.strip():
            parts.append(f'{session_heading(self.started)}\n{current.strip()}')
        return '\n\n'.join(parts)

    def delete_history(self):
        """Delete every kept session, this one included, and clear the captions."""
        for _, path in engine.history_files():
            try:
                path.unlink()
            except OSError as e:
                LOG.warning('Could not delete the transcript %s: %s', path, e)
        self.clear_transcript()
        if self.worker and self.settings.get('history'):
            self.worker.set_history(self.history_path)  # what is said next starts a new file
        if self.transcript:
            self.transcript.reload()

    def on_colour(self, action, value):
        if value.get_string() not in COLOURS:
            return
        action.set_state(value)
        self.settings.set('colour', value.get_string())
        if self.window:
            self.window.restyle()

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
        engine.prune_history()
        if self.settings.get('history'):
            self.worker.set_history(self.history_path)
        self.worker.start()
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

    def threads_finished(self, timeout):
        """After the main loop, wait for the recognition worker and the capture to leave native
        code (never from the window's shutdown: a stalled model must not freeze it). Python 3.12
        and 3.13 end a daemon thread that is still in native code when the interpreter exits
        with pthread_exit, and sherpa-onnx's C++ code then aborts the process."""
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout)
        return self.capture.wait_stopped(timeout) and not (self.worker and self.worker.is_alive())

    def request_quit(self):
        """Keep accepted saves alive until their snapshot and async write finish."""
        if self.pending_saves:
            self.quit_requested = True
            if self.window:
                self.window.show_status('Finishing transcript save before quitting…', sticky=True)
            return
        self.quit()

    def quit(self):
        """Fade the caption box out, then quit. A second request, or a fade that stalls
        because the screen stopped drawing, quits at once."""
        if self.window and not self.fading_out:
            self.fading_out = True
            GLib.timeout_add(FADE_OUT_MS + 350, self.quit)
            self.window.fade(self.window.box, None, 0.0, FADE_OUT_MS, self.quit)
            return False
        Adw.Application.quit(self)
        return False

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
                if self.transcript:
                    self.transcript.changed()
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
        elif kind == 'overload':
            self.window.show_status(f'Falling behind: skipped {value:.1f} s of audio', seconds=4)
        elif kind == 'load-error':
            self.ready = False
            self.window.show_failure('model', f'{value} Right-click and choose a sound source to try again.')
        elif kind == 'error':
            source_gen, stream_gen, message = value
            if (source_gen != self.source_gen or
                    stream_gen != getattr(self.worker, 'stream_gen', stream_gen) or
                    not getattr(self.worker, 'broken', True)):
                return False  # an obsolete source/stream cannot spend the current retry budget
            self.cancel_recognition_retry()
            self.recognition_failures += 1
            if self.recognition_failures <= 3:
                delay = 2 ** self.recognition_failures
                self.window.show_failure('recognition', f'{message}. Restarting in {delay} s…')
                self.recognition_retry_id = GLib.timeout_add_seconds(
                    delay, self.retry_recognition, self.recognition_retry_token, self.source_gen)
            else:
                self.window.show_failure('recognition', f'{message}. Right-click and choose a sound source to try again.')
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
            text = self.full_transcript(text)
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
                self.worker.snapshot(lambda text: self.write_transcript(f, self.full_transcript(text), tracked=True),
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
        """Queue saves to each destination so an older write cannot replace newer text."""
        if not tracked:
            self.begin_save()
        key = f.get_uri()
        queue = self.save_queues.setdefault(key, [])
        queue.append((f, text))
        if len(queue) == 1:
            self.start_save(key)

    def start_save(self, key):
        """Write one queued snapshot asynchronously; other destinations can proceed in parallel."""
        queue = self.save_queues[key]
        f, text = queue[0]

        def finished(success):
            queue.pop(0)
            if not queue:
                del self.save_queues[key]
            self.finish_save(success)
            if queue:
                self.start_save(key)

        def failed(error):
            LOG.warning('Saving the transcript failed: %s', error)
            self.window.show_status(f'Could not save the transcript: {error}', seconds=8)
            finished(False)

        def written(f, result):
            try:
                f.replace_contents_finish(result)
            except GLib.Error as e:
                failed(e.message)
                return
            self.window.show_status(f'Transcript saved to {f.get_basename()}.', seconds=4)
            finished(True)
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
        about.add_legal_section('Speech model', '© NVIDIA (stt_en_fastconformer_hybrid_large_streaming_multi); '
                                'fine-tuned by NowCaption',
                                Gtk.License.CUSTOM, 'Creative Commons Attribution 4.0 International (CC-BY-4.0). '
                                'Fine-tuned by NowCaption on CC-BY and CC0 English speech with text from NVIDIA '
                                'Parakeet TDT 0.6B v2 (CC-BY-4.0), then exported to ONNX and quantized to int8. '
                                'https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi')
        about.add_legal_section('Punctuation model', 'Edge-Punct-Casing by frankyoujian', Gtk.License.APACHE_2_0, None)
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
    app = NowCaption()
    status = app.run(sys.argv)
    if not app.threads_finished(3):  # still loading a model, for example: exit without finalizing
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(status)
    return status
