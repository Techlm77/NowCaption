"""NowCaption: on-device live captions for anything your computer plays, or your microphone."""
import bisect
import difflib
import json
import logging
import math
import os
import re
import sys
import time
import weakref
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
import cairo  # noqa: E402
import numpy as np  # noqa: E402
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
UNDOCK_MS, DOCK_MS = 220, 260  # docked captions slide into their edge, and out of the new one
# How new words arrive, chosen under More options. The pixel effects cut the word into small
# pieces cut from the text itself, so the pieces put together are exactly the word.
WORD_EFFECTS = {'none': ('None', 'Each word appears at once'),
                'fade': ('Fade', 'Each word fades in'),
                'assemble': ('Pixel assemble', 'Left to right, the pixels fade in as they fly into place'),
                'dissolve': ('Pixel dissolve', 'The pixels switch on in random order'),
                'mosaic': ('Mosaic sharpen', 'The word starts as big blocks and sharpens'),
                'rain': ('Pixel rain', 'The pixels drop into place from above, left to right')}
WORD_SPEEDS = {'fast': ('Fast', 0.6), 'normal': ('Normal', 1.0), 'slow': ('Slow', 1.75)}
PIXEL_MS = 200  # a pixel effect at normal speed (the fade takes WORD_MS)
PIXEL_WORDS = 8  # more words than this arriving at once (a whole passage) fade in instead
MOSAIC = (0.07, 0.11, 0.16, 0.24, 0.36, 0.55, 1.0)  # the mosaic's steps, as a share of full detail
BREATH_STEP_MS = 40  # breathing is slow and may last long: 25 steps a second, not every frame
# Where the captions go: floating where they are dragged, or docked along an edge of the screen.
# Docked, they reserve that strip of the screen (the layer-shell exclusive zone), so maximised and
# snapped windows end where the captions begin; that needs layer shell (Plasma, Hyprland, Sway).
POSITIONS = {'floating': 'Floating', 'top': 'Docked at the top', 'bottom': 'Docked at the bottom',
             'left': 'Docked on the left', 'right': 'Docked on the right'}
INNER = {'top': 's', 'bottom': 'n', 'left': 'e', 'right': 'w'}  # the edge a docked strip resizes by
ALONG = {'top': ('left', 'right'), 'bottom': ('left', 'right'), 'left': ('top', 'bottom'),
         'right': ('top', 'bottom')}  # the edges a docked strip also reaches
DEFAULTS = {'source': 'desktop', 'size': 30, 'opacity': 0.78, 'x': -1, 'y': -1, 'monitor': '',
            'width': -1, 'rows': ROWS, 'colour': 'white', 'history': True,
            'position': 'floating', 'dock_rows': ROWS, 'dock_width': -1,
            'word_effect': 'fade', 'word_speed': 'normal'}
LIMITS = {'size': (SIZES[0], SIZES[-1]), 'opacity': (0.2, 1.0), 'rows': (1, 100), 'dock_rows': (1, 100)}
CHOICES = {'source': ('desktop', 'mic'), 'colour': tuple(COLOURS), 'position': tuple(POSITIONS),
           'word_effect': tuple(WORD_EFFECTS), 'word_speed': tuple(WORD_SPEEDS)}


# On KDE Plasma, docked captions float over a fullscreen app on their screen (which hides the
# panels they line up with), and dock again when it leaves fullscreen. Wayland does not tell an
# app about other windows, so a small KWin script, loaded while the captions are docked, reports
# whether the active window on the captions' screen is fullscreen.
FULLSCREEN_PATH = '/io/github/Techlm77/NowCaption/Fullscreen'
FULLSCREEN_IFACE = 'io.github.Techlm77.NowCaption.Fullscreen'
FULLSCREEN_XML = f"""<node><interface name="{FULLSCREEN_IFACE}">
  <method name="Changed"><arg type="b" name="fullscreen" direction="in"/></method>
</interface></node>"""
KWIN_SCRIPT = """// NowCaption: whether the active window on the captions' screen is fullscreen.
const OUTPUT = %s;
let last = null;
function update() {
    const w = workspace.activeWindow;
    const full = !!(w && w.fullScreen && (OUTPUT === "" || (w.output && w.output.name === OUTPUT)));
    if (full !== last) {
        last = full;
        callDBus(%s, %s, %s, "Changed", full);
    }
}
function watch(w) { if (w) w.fullScreenChanged.connect(update); }
workspace.windowList().forEach(watch);
workspace.windowAdded.connect(function (w) { watch(w); update(); });
workspace.windowRemoved.connect(update);
workspace.windowActivated.connect(update);
update();
"""


class FullscreenWatch:
    """Loads and unloads NowCaption's KWin script over D-Bus. Nothing happens where KWin is not
    running: the calls just fail."""
    NAME = 'nowcaption-fullscreen'

    def __init__(self, app_id):
        self.app_id, self.output, self.bus = app_id, None, None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            pass

    def call(self, method, args, done=None, sync=False):
        if not self.bus:
            return
        params = GLib.Variant(args[0], args[1]) if args else None
        if sync:
            try:
                self.bus.call_sync('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting', method, params,
                                   None, Gio.DBusCallFlags.NO_AUTO_START, 500, None)
            except GLib.Error:
                pass
            return

        def finished(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error:
                return
            if done:
                done()
        self.bus.call('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting', method, params,
                      None, Gio.DBusCallFlags.NO_AUTO_START, 2000, None, finished)

    def start(self, output):
        """Watch the screen with this connector name ('' for any)."""
        if output == self.output or not self.bus:
            return
        self.output = output
        path = Path(GLib.get_user_data_dir()) / 'nowcaption' / 'kwin-fullscreen.js'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(KWIN_SCRIPT % tuple(map(json.dumps, (output, self.app_id, FULLSCREEN_PATH,
                                                                   FULLSCREEN_IFACE))))
        except OSError as e:
            LOG.warning('Could not write the KWin script: %s', e)
            return
        self.call('unloadScript', ('(s)', (self.NAME,)), lambda: self.call(
            'loadScript', ('(ss)', (str(path), self.NAME)), lambda: self.call('start', None)))

    def stop(self, sync=False):
        if self.output is not None:
            self.output = None
            self.call('unloadScript', ('(s)', (self.NAME,)), sync=sync)


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


def bare(word):
    """A word without the punctuation and capital the punctuation model may still change."""
    return word.strip('.,?!;:"\'').lower()


class Rows:
    """Wraps the caption passage into rows that stay put. Rows already shown keep their breaks
    while their words stay the same, even when the passage drops its oldest line (it holds the
    last Captioner.DISPLAY_LINES lines) or the punctuation model changes a comma or capital.
    Wrapping the whole passage afresh moved words between rows on screen at every finished
    line, once a long conversation filled the passage. New rows wrap a little short of the
    width, so that a comma or capital added later still fits."""

    def __init__(self, layout, row_height):
        self.width = layout.get_width()  # Pango units
        self.wrap = layout.copy()
        self.wrap.set_width(self.width - max(4, row_height // 2) * Pango.SCALE)
        self.wrap.set_wrap(Pango.WrapMode.WORD_CHAR)
        self.fit = layout.copy()
        self.fit.set_width(-1)
        self.words, self.spans = [], []  # the passage's words and each row's [start, end) in them

    def __call__(self, words):
        """The rows for the passage's words, as text."""
        old, new = [bare(w) for w in self.words], [bare(w) for w in words]
        d = self.dropped(old, new)
        spans = []
        if d is not None and self.spans:
            same = d  # old words up to here are still the passage's, maybe punctuated anew
            while same < len(old) and same - d < len(new) and old[same] == new[same - d]:
                same += 1
            first = next((i for i, (start, _) in enumerate(self.spans) if start >= d), None)
            if first is not None:
                pos = self.spans[first][0] - d
                spans = self.greedy(words, 0, pos)  # what is left of a row cut by the drop
                for start, end in self.spans[first:-1]:  # the last row is still being written
                    row = words[start - d:end - d]
                    if end > same or (row != self.words[start:end] and not self.fits(row)):
                        break
                    spans.append((start - d, end - d))
                    pos = end - d
                spans += self.greedy(words, pos, len(words))
        if not spans:
            spans = self.greedy(words, 0, len(words))
        self.words, self.spans = list(words), spans
        return [' '.join(words[start:end]) for start, end in spans]

    @staticmethod
    def dropped(old, new):
        """How many words the passage lost at its start, or None if the new passage does not
        continue the old one."""
        key = new[:min(8, len(old))]
        if not key:
            return None
        for d in range(len(old) - len(key) + 1):
            if old[d:d + len(key)] == key:
                return d
        return None

    def greedy(self, words, a, b):
        """Rows for words[a:b], as Pango wraps them."""
        if a >= b:
            return []
        text, starts, pos = ' '.join(words[a:b]), [], 0
        for w in words[a:b]:
            starts.append(pos)
            pos += len(w.encode()) + 1
        self.wrap.set_text(text, -1)
        firsts = [bisect.bisect_left(starts, line.start_index) for line in self.wrap.get_lines_readonly()]
        bounds = sorted(set(firsts + [b - a]))
        return [(a + s, a + e) for s, e in zip(bounds, bounds[1:]) if s < e]

    def fits(self, row):
        self.fit.set_text(' '.join(row), -1)
        return self.fit.get_size()[0] <= self.width


def ease_out(p):
    return 1 - (1 - p) ** 3


class Pieces:
    """A new word cut into small square pieces, for the pixel effects. The word is drawn once,
    as the label draws it, and each piece keeps the text's own smooth edges, so once every
    piece has landed they show exactly the word. Pieces with no ink are left out. Each frame
    puts all the pieces into one image, with numpy, rather than drawing them one by one."""

    def __init__(self, layout, colour, box, scale, row_height):
        """layout: the label's layout without the fading; box: the word's (x, y, width, height)
        in it, px; scale: the screen's pixels per px."""
        x, y, w, h = box
        self.left, self.top = math.floor(x) - 1, math.floor(y)  # whole px, so landed pieces sit on pixels
        self.width, self.height = math.ceil(x + w) + 1 - self.left, math.ceil(y + h) - self.top
        snap = Gtk.Snapshot()
        snap.scale(scale, scale)
        snap.translate(Graphene.Point().init(-self.left, -self.top))
        snap.push_clip(Graphene.Rect().init(self.left, self.top, self.width, self.height))
        snap.append_layout(layout, colour)
        snap.pop()
        width, height = max(1, math.ceil(self.width * scale)), max(1, math.ceil(self.height * scale))
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        node = snap.to_node()
        if node:
            node.draw(cairo.Context(self.surface))
        self.surface.flush()
        self.surface.set_device_scale(scale, scale)
        # pieces about 1/17 of a row across: the same look, and about as many, at any text size
        self.scale, self.cell = scale, max(1, round(max(2, round(row_height / 17)) * scale))  # screen pixels
        cell, self.size = self.cell, self.cell / scale
        rows, cols = -(-height // cell), -(-width // cell)
        word = np.zeros((rows * cell, cols * cell, 4), np.uint8)
        pixels = np.frombuffer(self.surface.get_data(), np.uint8).reshape(height, -1)[:, :width * 4]
        word[:height, :width] = pixels.reshape(height, width, 4)
        cells = word.reshape(rows, cell, cols, cell, 4).transpose(0, 2, 1, 3, 4)
        r, c = np.nonzero(cells[..., 3].max(axis=(2, 3)))
        self.cells, n = cells[r, c], len(r)  # each piece's pixels, as cairo keeps them
        self.x, self.y = c * self.size, r * self.size  # each piece's place in the word, px
        self.across = self.x / self.width  # 0 at the word's left edge, nearly 1 at its right
        rng = np.random.default_rng()
        angle, reach = rng.uniform(0, 2 * math.pi, n), rng.uniform(0.4, 1.5, n) * row_height
        self.dx, self.dy = np.cos(angle) * reach, np.sin(angle) * reach * 0.7  # where assemble starts
        self.delay = rng.uniform(0, 0.06, n)  # so assemble's left-to-right front is not a hard edge
        self.order = rng.random(n)  # when dissolve switches each piece on
        self.drop = rng.uniform(0.5, 1.4, n) * row_height  # how far rain drops from
        self.margin = round((1.5 * row_height + self.size) * scale)  # screen pixels around the word
        self.mosaics, self.key = {}, None

    SHARP = 0.55  # the mosaic has its full detail by here, then blends into the label

    @classmethod
    def blend(cls, p):
        """How far the mosaic has blended into the label, p of the way through."""
        return max(0.0, (p - cls.SHARP) / (1 - cls.SHARP))

    def bounds(self, x, y):
        """Where the pieces can be while the word lands at (x, y), as x, y, width, height."""
        m = self.margin / self.scale
        return x + self.left - m, y + self.top - m, self.width + 2 * m, self.height + 2 * m

    def state(self, effect, p):
        """Each piece p of the way (0 to 1) through a pixel effect: whether it shows, how far
        it is from its place (px), and its opacity. The pieces rush in and then settle, so most
        of the word can be read before half the time is up; all have landed by 0.75."""
        zero = np.zeros(len(self.x))
        if effect == 'assemble':
            t = p - (self.across * 0.25 + self.delay)
            q = 1 - (1 - np.clip(t / 0.4, 0, 1)) ** 5
            return t > 0, self.dx * (1 - q), self.dy * (1 - q), np.clip(t / 0.1, 0, 1)
        if effect == 'rain':
            t = (p - self.across * 0.25) / 0.45
            q = 1 - (1 - np.clip(t, 0, 1)) ** 5
            return t > 0, zero, -self.drop * (1 - q), q
        return p >= self.order * 0.5, zero, zero, zero + 1  # dissolve

    def draw(self, cr, effect, p, x, y):
        """Draw the word p of the way (0 to 1) through its effect, landing at (x, y)."""
        x, y = x + self.left, y + self.top
        if effect == 'mosaic':
            step = MOSAIC[min(len(MOSAIC) - 1, int(p / self.SHARP * len(MOSAIC)))]
            small = self.mosaics.get(step)
            if small is None:
                w = max(1, round(self.surface.get_width() * step))
                h = max(1, round(self.surface.get_height() * step))
                small = self.mosaics[step] = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
                c = cairo.Context(small)
                c.scale(w / self.width, h / self.height)
                c.set_source_surface(self.surface, 0, 0)
                c.paint()
            cr.save()
            cr.translate(x, y)
            cr.scale(self.width / small.get_width(), self.height / small.get_height())
            cr.set_source_surface(small, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_NEAREST)
            cr.paint_with_alpha(min(1.0, p * 6) * (1 - self.blend(p)))
            cr.restore()
            return
        shown, ox, oy, alpha = self.state(effect, p)
        # Put every piece in one image, on whole screen pixels (landed, exactly where the word's are)
        m, cell = self.margin, self.cell
        width, height = self.surface.get_width() + 2 * m, self.surface.get_height() + 2 * m
        px = m + np.rint((self.x + ox) * self.scale).astype(np.intp)
        py = m + np.rint((self.y + oy) * self.scale).astype(np.intp)
        k = np.nonzero(shown & (px >= 0) & (py >= 0) & (px + cell <= width) & (py + cell <= height))[0]
        if not len(k):
            return
        pixels = self.cells[k]
        if (alpha[k] < 1).any():
            pixels = (pixels * alpha[k, None, None, None]).astype(np.uint8)  # premultiplied: all four
        span = np.arange(cell)
        frame = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        image = np.ndarray((height, frame.get_stride() // 4, 4), np.uint8, frame.get_data())
        image[py[k, None, None] + span[None, :, None], px[k, None, None] + span[None, None, :]] = pixels
        frame.mark_dirty()
        frame.set_device_scale(self.scale, self.scale)
        cr.set_source_surface(frame, x - m / self.scale, y - m / self.scale)
        cr.paint()


class StyledLabel(Gtk.Label):
    """A Gtk.Label that calls styled() once GTK has applied new CSS to it. GTK applies a new
    font size only when it next lays the window out, after restyle() has measured the font."""

    def __init__(self, styled, **kwargs):
        super().__init__(**kwargs)
        self.styled = styled

    def do_css_changed(self, change):
        Gtk.Label.do_css_changed(self, change)
        self.styled()


class Stage(Gtk.Widget):
    """The window's content. Floating, it holds the caption box at x, y at the box's own size,
    inside an overlay covering the screen. Docked, the box fills the window, whose size along
    the docked edge the compositor sets from the room beside any panels: the window must not
    ask for more, or it would cover them."""

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
        at = 0 if self.fill else (self.x if orientation == Gtk.Orientation.HORIZONTAL else self.y)
        return minimum + at, natural + at, -1, -1

    def do_size_allocate(self, width, height, baseline):
        w_min, w_nat, _, _ = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)
        if self.fill:
            w = max(width, w_min)
            self.child.allocate(w, max(height, self.child.measure(Gtk.Orientation.VERTICAL, w)[0]), -1, None)
            return
        h_nat = self.child.measure(Gtk.Orientation.VERTICAL, w_nat)[1]
        at = Gsk.Transform().translate(Graphene.Point().init(self.x, self.y))
        self.child.allocate(w_nat, h_nat, -1, at)


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


class CaptionText(Gtk.Widget):
    """The caption rows: a Gtk.Label, which screen readers read, clipped to exactly the rows.
    When the passage moves up, the rows glide up instead of jumping (the label then shows the
    rows on their way out too, moved up by self.offset). New words arrive with the chosen effect:
    they fade in, or the label hides them while their Pieces are drawn over it, until every
    piece has landed. A status message shown in place of captions fades in, and breathes while
    it waits for speech. Without animations, the label shows just the last rows."""

    def __init__(self, label, **kwargs):
        super().__init__(overflow=Gtk.Overflow.HIDDEN, **kwargs)
        self.label = label
        label.set_parent(self)
        self.layout, self.rows, self.row_height, self.wrapper = None, ROWS, 0, None
        self.flexible = False  # docked at a side, the compositor's height decides the rows
        self.text, self.lines, self.words, self.status = '', [], [], False
        self.glide, self.glide_start = 0.0, 0  # rows to glide up, counted from glide_start (µs)
        self.appeared = {}  # passage word index: when it appeared (µs), while it arrives
        self.effect, self.speed = 'fade', 1.0  # how new words arrive, and the speed's factor
        self.pieces, self.drawing = {}, []  # passage word index: its Pieces; what to draw, with progress
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
            return self.row_height if self.flexible else height, height, -1, -1
        return 0, self.label.measure(orientation, -1)[1], -1, -1

    def do_size_allocate(self, width, height, baseline):
        width = max(width, self.label.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        height = max(height, self.label.measure(Gtk.Orientation.VERTICAL, width)[0])
        up = Gsk.Transform().translate(Graphene.Point().init(0, -self.offset)) if self.offset else None
        self.label.allocate(width, height, -1, up)

    def configure(self, layout, rows, row_height, flexible=False):
        """A new width, font or number of rows: the rows are shown at rest, wrapped afresh."""
        self.layout, self.rows, self.row_height, self.flexible = layout, rows, row_height, flexible
        self.wrapper = Rows(layout, row_height)
        self.glide, self.appeared, self.pieces = 0.0, {}, {}
        self.queue_resize()

    def set_effect(self, effect, speed):
        """How new words arrive (a WORD_EFFECTS name), and the factor of its WORD_SPEEDS speed."""
        self.effect, self.speed, self.pieces = effect, speed, {}

    def word_us(self):
        """How long a new word takes to arrive, in µs."""
        if self.effect == 'none':
            return 0
        return (WORD_MS if self.effect == 'fade' else PIXEL_MS) * self.speed * 1000

    def show(self, text, status=False, waiting=False):
        """Show the passage, or with status, a status message (waiting: one that waits for
        speech, so it breathes)."""
        now = GLib.get_monotonic_time()
        words = text.split()
        lines = self.wrapper(words) if self.wrapper else []
        if self.get_mapped() and Adw.get_enable_animations(self):
            moved = self.moved(lines)
            if moved is None or moved > self.rows:
                self.glide = 0.0
            elif moved:
                self.glide, self.glide_start = self.gliding(now) + moved, now
            fresh = self.status or not self.words
            self.appeared = {} if status or self.effect == 'none' else self.carry(words, now, fresh)
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
        """Show the rows, the words arriving and the status message as of now (µs). True
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
        self.appeared = {i: t for i, t in self.appeared.items() if now - t < self.word_us()}
        attrs, drawing = self.arrivals(text, now) if self.appeared else (None, [])
        if self.appeared or self.attributed:
            self.label.set_attributes(attrs)
            self.attributed = bool(self.appeared)
        if drawing or self.drawing:
            self.queue_draw()
        self.drawing = drawing
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

    def arrivals(self, text, now):
        """Pango attributes for the words arriving in the shown text: faded in, or hidden while
        their pieces are drawn over the label; and those pieces, as (Pieces, progress 0 to 1)."""
        spans = [m.span() for m in re.finditer(r'\S+', text)]
        first = len(self.words) - len(spans)  # the passage index of the first word shown
        burst = {}  # words that appeared together, such as a whole passage after a status message
        for t in self.appeared.values():
            burst[t] = burst.get(t, 0) + 1
        attrs, drawing, pieces, layout = Pango.AttrList(), [], {}, None
        for i, t in self.appeared.items():
            if not 0 <= i - first < len(spans):
                continue
            a, b = spans[i - first]
            start, end = len(text[:a].encode()), len(text[:b].encode())
            p = alpha = min(1.0, max(0.0, (now - t) / self.word_us()))
            if self.effect != 'fade' and burst[t] <= PIXEL_WORDS:
                if layout is None:
                    layout = self.label.get_layout().copy()
                    layout.set_attributes(None)
                word = self.word_pieces(i, layout, text[a:b], start, end)
                if word:
                    pieces[i] = word
                    drawing.append((word, p))
                    alpha = Pieces.blend(p) if self.effect == 'mosaic' else 0.0
            attr = Pango.attr_foreground_alpha_new(max(1, int(65535 * alpha)))  # 0 means opaque
            attr.start_index, attr.end_index = start, end
            attrs.insert(attr)
        self.pieces = pieces
        return attrs, drawing

    def word_pieces(self, i, layout, word, start, end):
        """The Pieces of passage word i, shown as word between byte offsets start and end of
        the layout; made again if it moved or the colour changed."""
        first, last = layout.index_to_pos(start), layout.index_to_pos(end)
        box = (first.x / Pango.SCALE, first.y / Pango.SCALE, (last.x - first.x) / Pango.SCALE,
               first.height / Pango.SCALE)
        native = self.get_native()
        surface = native.get_surface() if native else None
        scale = surface.get_scale() if surface else self.get_scale_factor()
        colour = self.label.get_color()
        key = (word, tuple(round(v * 8) for v in box), colour.to_string(), scale)
        pieces = self.pieces.get(i)
        if pieces is None or pieces.key != key:
            try:
                pieces = Pieces(layout, colour, box, scale, self.row_height)
            except (cairo.Error, GLib.Error, ValueError) as e:
                LOG.debug('Could not cut %r into pieces, fading it in: %s', word, e)
                return None
            pieces.key = key
        return pieces

    def do_snapshot(self, snapshot):
        Gtk.Widget.do_snapshot(self, snapshot)  # the label
        width, height = self.get_width(), self.get_height()
        for pieces, p in self.drawing:  # over the label, moved up with it while the rows glide
            x, y, w, h = pieces.bounds(0, -self.offset)
            x0, y0, x1, y1 = max(0, x), max(0, y), min(width, x + w), min(height, y + h)
            if x1 > x0 and y1 > y0:
                cr = snapshot.append_cairo(Graphene.Rect().init(x0, y0, x1 - x0, y1 - y0))
                pieces.draw(cr, self.effect, p, 0, -self.offset)

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
        self.dragging = self.resizing = self.held = None
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
        """While the captions are docked, have KWin say when an app on their screen is fullscreen."""
        if self.settings.get('position') == 'floating':
            self.app.fullscreen_watch.stop()
        else:
            self.app.fullscreen_watch.start((self.monitor.get_connector() or '') if self.monitor else '')

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
            .caption-preview {{ background: rgba(0, 0, 0, {alpha}); border-radius: 14px; }}
            .caption-preview .caption-text {{ color: {colour}; font-size: {min(px, Preview.MAX_SIZE)}px;
                                             font-weight: 500; transition: color {CHANGE_MS}ms ease-out; }}
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
        elif self.edge:
            width = self.settings.get('dock_width')
            self.box_width = width if width > 0 else self.dock_width_for(self.monitor)
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
        surface = self.get_surface()  # the window manager moves normal windows
        if isinstance(surface, Gdk.Toplevel):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            surface.begin_move(gesture.get_device(), gesture.get_current_button(), x, y,
                               gesture.get_current_event_time())

    def on_drag_update(self, gesture, dx, dy):
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


class Preview(Gtk.Box):
    """A sample caption under More options, in the captions' colour and background, that keeps
    adding words like speech, so each way for new words to arrive can be seen before choosing it."""

    WIDTH, ROWS, MAX_SIZE, EVERY_MS = 420, 2, 26, 380  # text width (px), rows, largest text (px), a word every
    SAMPLE = ('So we took the long road back past the old sawmill, and it was getting dark. The radio '
              'kept cutting out, then we saw headlights behind us, way too close.').split()

    def __init__(self, app):
        super().__init__(css_classes=['caption-preview'], halign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.update_state([Gtk.AccessibleState.HIDDEN], [True])  # sample text, not captions
        self.label = StyledLabel(weakly(self.fit), xalign=0, yalign=0, wrap=False, single_line_mode=False)
        self.label.add_css_class('caption-text')
        self.caption = CaptionText(self.label, margin_start=PAD_X, margin_end=PAD_X,
                                   margin_top=PAD_Y, margin_bottom=PAD_Y)
        self.caption.set_size_request(self.WIDTH, -1)
        self.caption.set_effect(*app.word_effect())
        self.append(self.caption)
        self.shown, self.row_height, self.timer = 0, None, 0
        self.fit()

    def do_map(self):
        Gtk.Box.do_map(self)
        self.play(True)

    def do_unmap(self):
        self.play(False)
        Gtk.Box.do_unmap(self)

    def fit(self):
        """Wrap the sample to the preview, in the font GTK has applied to it."""
        height = self.label.create_pango_layout('Ag').get_pixel_size()[1]
        if height != self.row_height:
            self.row_height = height
            layout = self.label.create_pango_layout('')
            layout.set_width(self.WIDTH * Pango.SCALE)
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            self.caption.configure(layout, self.ROWS, height)
            self.caption.show(' '.join(self.SAMPLE[:self.shown]))

    def play(self, on):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if on:
            self.timer = GLib.timeout_add(self.EVERY_MS, self.next_word)

    def next_word(self):
        if self.shown == len(self.SAMPLE):  # start again, every word new
            self.shown = 0
            self.caption.show('')
        self.shown += 1
        self.caption.show(' '.join(self.SAMPLE[:self.shown]))
        return GLib.SOURCE_CONTINUE


def weakly(method):
    """method, as a handler GTK can hold without keeping method's object alive. Python's garbage
    collector cannot see GTK's references, so a cycle through one, such as a window's button that
    calls the window, would keep a closed window forever."""
    ref = weakref.WeakMethod(method)

    def call(*args):
        live = ref()
        return live(*args) if live else None
    return call


class OptionsWindow(Adw.Window):
    """More options, for what the caption box's menu has no room for: what to listen to, and
    how new words arrive (with a sample caption showing it) and how fast. Choices show in the
    sample first and reach the captions only with Apply; closing without it drops them."""

    SETTINGS = {'source': 'source', 'word-effect': 'word_effect', 'word-speed': 'word_speed'}  # action: key

    def __init__(self, app):
        super().__init__(application=app, title='More options', default_width=560, default_height=760)
        self.app = app
        self.chosen = Gio.SimpleActionGroup()  # the choices made here, until Apply
        for name, key in self.SETTINGS.items():
            action = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new('s'),
                                                   GLib.Variant('s', app.settings.get(key)))
            action.connect('activate', weakly(self.on_choice))
            self.chosen.add_action(action)
        self.insert_action_group('options', self.chosen)
        page = Adw.PreferencesPage()
        listen = Adw.PreferencesGroup(title='Listen to')
        self.choices(listen, 'options.source', (('desktop', 'Desktop audio', 'Everything your computer plays'),
                                                ('mic', 'Microphone', 'Your microphone, for the people around you')))
        # an action, not a choice: it listens again at once, without Apply
        restart = Gtk.Button(label='Restart', action_name='app.restart-listening', valign=Gtk.Align.CENTER)
        again = Adw.ActionRow(title='Start again', subtitle='After a problem, or when you change sound devices',
                              activatable_widget=restart)
        again.add_suffix(restart)
        listen.add(again)
        page.add(listen)
        self.preview = Preview(app)
        sample = Adw.PreferencesGroup(title='New words', description='How each new word appears in the captions')
        sample.add(self.preview)
        page.add(sample)
        words = Adw.PreferencesGroup()
        self.choices(words, 'options.word-effect', ((name, title, subtitle)
                                                   for name, (title, subtitle) in WORD_EFFECTS.items()))
        speeds = Gtk.Box(css_classes=['linked'], valign=Gtk.Align.CENTER)
        for name, (label, _) in WORD_SPEEDS.items():
            speeds.append(Gtk.ToggleButton(label=label, action_name='options.word-speed',
                                           action_target=GLib.Variant('s', name)))
        self.speed = Adw.ActionRow(title='Speed')
        self.speed.add_suffix(speeds)
        words.add(self.speed)
        page.add(words)
        motion = Adw.PreferencesGroup(description='With animations turned off for your desktop, new words '
                                                  'appear at once, whichever way is chosen.')
        page.add(motion)
        self.apply_button = Gtk.Button(label='Apply', css_classes=['suggested-action'],
                                       tooltip_text='Use these choices for the captions')
        self.apply_button.connect('clicked', weakly(self.apply))
        header = Adw.HeaderBar()
        header.pack_end(self.apply_button)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self.set_content(view)
        close = Gtk.ShortcutController()
        close.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string('Escape'),
                                            Gtk.NamedAction.new('window.close')))
        self.add_controller(close)
        self.show_choices()

    def choice(self, name):
        return self.chosen.lookup_action(name).get_state().get_string()

    def changed(self):
        """The choices that differ from what the captions use."""
        return [name for name, key in self.SETTINGS.items() if self.choice(name) != self.app.settings.get(key)]

    def on_choice(self, action, value):
        action.set_state(value)
        self.show_choices()

    def show_choices(self):
        effect = self.choice('word-effect')
        self.preview.caption.set_effect(effect, WORD_SPEEDS[self.choice('word-speed')][1])
        self.speed.set_sensitive(effect != 'none')  # no speed for words that appear at once
        self.apply_button.set_sensitive(bool(self.changed()))

    def apply(self, button=None):
        for name in self.changed():  # only those, so the same source does not restart capture
            self.app.lookup_action(name).activate(GLib.Variant('s', self.choice(name)))
        self.show_choices()

    @staticmethod
    def choices(group, action, options):
        """A row for each (value, title, subtitle), chosen with a radio button bound to action."""
        first = None
        for value, title, subtitle in options:
            check = Gtk.CheckButton(action_name=action, action_target=GLib.Variant('s', value),
                                    valign=Gtk.Align.CENTER)
            if first:
                check.set_group(first)
            first = first or check
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable_widget=check)
            row.add_prefix(check)
            group.add(row)


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
        self.options = None  # the More options window, while it is open
        self.save_queues = {}  # destination URI -> accepted writes, in snapshot order
        self.quit_requested = False
        self.fading_out = False
        self.fullscreen_watch = FullscreenWatch(APP_ID)
        self.capture = audio.Capture(
            on_audio=lambda gen, pcm: self.worker.feed(gen, pcm),
            on_started=lambda gen: GLib.idle_add(self.capture_started, gen),
            on_ended=lambda gen, reason: GLib.idle_add(self.capture_ended, gen, reason))
        self.connect('activate', self.on_activate)
        self.connect('shutdown', self.on_shutdown)
        self.add_actions()

    def build_menu(self, several_screens=False, layer=False):
        """The caption box's menu, in sections: how captions look, the box's place, the
        transcript, then More options (a window for the rest) and the app. CaptionWindow.update_menu
        fills the 'custom' items with rows of buttons that keep the menu open."""
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
        menu.append_item(section('Appearance', *(custom(name) for name in (*STEPPERS, 'text-colour'))))
        place = [item('Reset position and size', 'app.reset')]
        if layer:  # elsewhere the window manager places the window
            place.insert(0, custom('position'))
        if several_screens:
            place.append(item('Move to next screen', 'app.next-screen'))
        menu.append_item(section(None, *place))
        menu.append_item(section('Transcript', item('Open transcript', 'app.transcript'),
                                 item(f'Keep {engine.HISTORY_DAYS} days of history', 'app.history'),
                                 item('Clear this session', 'app.clear')))
        menu.append_item(section(None, item('More options…', 'app.options'), item('About NowCaption', 'app.about'),
                                 item('Quit', 'app.quit')))
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
        position = Gio.SimpleAction.new_stateful('position', GLib.VariantType.new('s'),
                                                 GLib.Variant('s', self.settings.get('position')))
        position.connect('activate', self.on_position)
        self.add_action(position)
        for name, key, handler in (('word-effect', 'word_effect', self.on_word_effect),
                                   ('word-speed', 'word_speed', self.on_word_speed)):
            action = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new('s'),
                                                   GLib.Variant('s', self.settings.get(key)))
            action.connect('activate', handler)
            self.add_action(action)
        history = Gio.SimpleAction.new_stateful('history', None, GLib.Variant('b', self.settings.get('history')))
        history.connect('activate', self.on_history)
        self.add_action(history)
        act('transcript', self.open_transcript, ['<Control>t'])
        act('options', self.open_options, ['<Control>comma'])
        act('restart-listening', self.restart_listening)
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

    def restart_listening(self):
        """Choose the same sound source again, which starts capture again, or loads the speech
        model again if it failed: More options' Restart, after a problem or a change of devices."""
        source = self.lookup_action('source')
        source.activate(source.get_state())

    def on_position(self, action, value):
        if value.get_string() not in POSITIONS:
            return
        action.set_state(value)
        self.settings.set('position', value.get_string())
        if self.window:
            self.window.reposition()

    def on_word_effect(self, action, value):
        if value.get_string() in WORD_EFFECTS:
            action.set_state(value)
            self.settings.set('word_effect', value.get_string())
            self.show_word_effect()

    def on_word_speed(self, action, value):
        if value.get_string() in WORD_SPEEDS:
            action.set_state(value)
            self.settings.set('word_speed', value.get_string())
            self.show_word_effect()

    def word_effect(self):
        """How new words arrive, and the factor of its speed, as CaptionText.set_effect takes them."""
        return self.settings.get('word_effect'), WORD_SPEEDS[self.settings.get('word_speed')][1]

    def show_word_effect(self):
        if self.window:
            self.window.caption.set_effect(*self.word_effect())

    def open_options(self):
        if self.options is None:
            self.options = OptionsWindow(self)
            self.options.connect('close-request', self.on_options_closed)
        self.options.present()

    def on_options_closed(self, window):
        self.options = None
        return False

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

    def do_dbus_register(self, connection, object_path):
        """Also take the KWin script's word on fullscreen apps (see FULLSCREEN_PATH)."""
        Adw.Application.do_dbus_register(self, connection, object_path)
        try:
            info = Gio.DBusNodeInfo.new_for_xml(FULLSCREEN_XML).interfaces[0]
            connection.register_object(FULLSCREEN_PATH, info, self.on_fullscreen_call, None, None)
        except GLib.Error as e:  # captions still work, just without floating over fullscreen apps
            LOG.warning('Could not listen for fullscreen apps: %s', e)
        return True

    def on_fullscreen_call(self, connection, sender, path, iface, method, params, invocation):
        invocation.return_value(None)
        if method == 'Changed':
            self.on_fullscreen(params.unpack()[0])

    def on_fullscreen(self, fullscreen):
        if self.window:
            self.window.set_covered(bool(fullscreen))

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
        self.fullscreen_watch.stop(sync=True)
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
            self.window.show_failure('model', f'{value} To try again, open More options and press Restart.')
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
                self.window.show_failure('recognition',
                                         f'{message}. To try again, open More options and press Restart.')
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
