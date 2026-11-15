"""Drawing the caption passage: rows that keep their breaks, the pieces of the pixel effects, and
the caption text widget that glides the rows up and brings new words in."""
import bisect
import difflib
import logging
import math
import re

import cairo
import gi
import numpy as np
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .style import (BREATH_MS, BREATH_STEP_MS, GLIDE_MS, MOSAIC, PIXEL_MS, PIXEL_WORDS, ROWS,  # noqa: E402
                    STATUS_MS, WORD_MS)

LOG = logging.getLogger('nowcaption')


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
        self.spot = r * 65536 + c  # each piece's cell in the word, as one number
        self.across = self.x / self.width  # 0 at the word's left edge, nearly 1 at its right
        rng = np.random.default_rng()
        angle, reach = rng.uniform(0, 2 * math.pi, n), rng.uniform(0.4, 1.5, n) * row_height
        self.dx, self.dy = np.cos(angle) * reach, np.sin(angle) * reach * 0.7  # where assemble starts
        self.delay = rng.uniform(0, 0.06, n)  # so assemble's left-to-right front is not a hard edge
        self.order = rng.random(n)  # when dissolve switches each piece on
        self.drop = rng.uniform(0.5, 1.4, n) * row_height  # how far rain drops from
        self.mosaics, self.key, self.placed = {}, None, (None, None)

    def keep(self, old):
        """Give the pieces the paths they had in old, the same word cut earlier, so a word cut
        again in flight (moved up a row when the rows glide, or in a colour that is changing)
        flies on instead of starting new paths. Pieces in cells old had no ink in keep their own."""
        if old.size != self.size:
            return
        _, mine, theirs = np.intersect1d(self.spot, old.spot, assume_unique=True, return_indices=True)
        for name in ('dx', 'dy', 'delay', 'order', 'drop'):
            getattr(self, name)[mine] = getattr(old, name)[theirs]

    SHARP = 0.55  # the mosaic has its full detail by here, then blends into the label

    @classmethod
    def blend(cls, p):
        """How far the mosaic has blended into the label, p of the way through."""
        return max(0.0, (p - cls.SHARP) / (1 - cls.SHARP))

    def bounds(self, effect, p, x, y):
        """Where the pieces are p of the way through effect while the word lands at (x, y), as
        x, y, width, height; None when none shows."""
        if effect == 'mosaic':
            return x + self.left, y + self.top, self.width, self.height
        k, px, py, _ = self.place(effect, p)
        if not len(k):
            return None
        left, top, s = px.min(), py.min(), self.scale
        return (x + self.left + left / s, y + self.top + top / s, (px.max() + self.cell - left) / s,
                (py.max() + self.cell - top) / s)

    def place(self, effect, p):
        """The pieces that show p of the way through a pixel effect: their numbers, where they
        are in screen pixels from the word's top left, whole ones (landed, exactly where the
        word's are), and their opacity. Kept for the frame, which asks bounds() before draw()."""
        if self.placed[0] != (effect, p):
            shown, ox, oy, alpha = self.state(effect, p)
            k = np.nonzero(shown)[0]
            px = np.rint((self.x[k] + ox[k]) * self.scale).astype(np.intp)
            py = np.rint((self.y[k] + oy[k]) * self.scale).astype(np.intp)
            self.placed = (effect, p), (k, px, py, alpha[k])
        return self.placed[1]

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
        k, px, py, alpha = self.place(effect, p)
        if not len(k):
            return
        # Put the pieces in one image, just big enough for them, on whole screen pixels
        cell, left, top = self.cell, px.min(), py.min()
        width, height = int(px.max() + cell - left), int(py.max() + cell - top)
        px, py = px - left, py - top
        pixels = self.cells[k]
        if (alpha < 1).any():
            pixels = (pixels * alpha[:, None, None, None]).astype(np.uint8)  # premultiplied: all four
        span = np.arange(cell)
        frame = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        image = np.ndarray((height, frame.get_stride() // 4, 4), np.uint8, frame.get_data())
        image[py[:, None, None] + span[None, :, None], px[:, None, None] + span[None, None, :]] = pixels
        frame.mark_dirty()
        frame.set_device_scale(self.scale, self.scale)
        cr.set_source_surface(frame, x + left / self.scale, y + top / self.scale)
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
        self.pitch = 0.0  # how far apart the label's lines are, px (see configure)
        self.flexible = False  # docked at a side, the compositor's height decides the rows
        self.text, self.lines, self.words, self.status = '', [], [], False
        self.glide, self.glide_start = 0.0, 0  # rows to glide up, counted from glide_start (µs)
        self.appeared = {}  # passage word index: when it appeared (µs), while it arrives
        self.effect, self.speed = 'fade', 1.0  # how new words arrive, and the speed's factor
        self.pieces, self.drawing = {}, []  # passage word index: its Pieces; what to draw, with progress
        self.shown_at = self.breath_start = None  # status message: when it appeared, began breathing
        self.offset, self.tick, self.breath_timer, self.attributed = 0.0, 0, 0, False
        self.watching = None  # while shown: the Gtk.Settings followed for animations, and the handler

    def do_dispose(self):
        self.stop()
        self.watch(False)
        if self.label.get_parent() is self:
            self.label.unparent()
        Gtk.Widget.do_dispose(self)

    def do_map(self):
        Gtk.Widget.do_map(self)
        self.watch(True)
        self.on_animations()

    def do_unmap(self):
        self.stop()
        self.watch(False)
        Gtk.Widget.do_unmap(self)

    def watch(self, on):
        """Follow the desktop turning animations on or off, only while shown, so the settings
        never keep a widget that is gone (the sample under More options) and its handler."""
        if self.watching:
            settings, handler = self.watching
            settings.disconnect(handler)
            self.watching = None
        if on:
            settings = self.get_settings()
            self.watching = settings, settings.connect('notify::gtk-enable-animations', self.on_animations)

    def on_animations(self, *args):
        """Shown, or the desktop's animations turned on or off. Without them, whatever still
        moves (the rows, new words, a status message's fade and breathing) shows its end at once."""
        if not Adw.get_enable_animations(self):
            self.glide, self.appeared, self.shown_at, self.breath_start = 0.0, {}, None, None
            self.stop()
        self.start()

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
        # The label's lines are this far apart: a fraction of a pixel less than the whole-pixel
        # row height, as fonts go. Gliding by it ends exactly where the rows come to rest.
        one, two = (self.label.create_pango_layout(text).get_size()[1] for text in ('Ag', 'Ag\nAg'))
        self.pitch = (two - one) / Pango.SCALE or row_height
        self.wrapper = Rows(layout, row_height)
        self.glide, self.appeared, self.pieces = 0.0, {}, {}
        self.queue_resize()

    def set_effect(self, effect, speed):
        """How new words arrive (a WORD_EFFECTS name), and the factor of its WORD_SPEEDS speed.
        With none, the words still arriving show at once."""
        self.effect, self.speed, self.pieces = effect, speed, {}
        if effect == 'none' and self.appeared:
            self.appeared = {}
            self.start()

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
        offset = (extra - min(glide, extra)) * self.pitch
        if offset != self.offset:
            self.offset = offset
            self.queue_allocate()
        self.appeared = {i: t for i, t in self.appeared.items() if now - t < self.word_us()}
        attrs, drawing = self.arrivals(text, now) if self.appeared else (None, [])
        if self.appeared or self.attributed:
            old = self.label.get_attributes()
            if attrs is None or old is None or not attrs.equal(old):  # unchanged all through a pixel flight
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
        attrs, drawing, pieces, layout, us = Pango.AttrList(), [], {}, None, self.word_us()
        for i, t in self.appeared.items():
            if not 0 <= i - first < len(spans):
                continue
            a, b = spans[i - first]
            start, end = len(text[:a].encode()), len(text[:b].encode())
            p = alpha = min(1.0, max(0.0, (now - t) / us)) if us else 1.0
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
        the layout; made again if it moved or the colour changed, the pieces flying on."""
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
            old = pieces
            try:
                pieces = Pieces(layout, colour, box, scale, self.row_height)
            except (cairo.Error, GLib.Error, ValueError) as e:
                LOG.debug('Could not cut %r into pieces, fading it in: %s', word, e)
                return None
            pieces.key = key
            if old:
                pieces.keep(old)
        return pieces

    def do_snapshot(self, snapshot):
        Gtk.Widget.do_snapshot(self, snapshot)  # the label
        width, height = self.get_width(), self.get_height()
        for pieces, p in self.drawing:  # over the label, moved up with it while the rows glide
            x, y, w, h = pieces.bounds(self.effect, p, 0, -self.offset) or (0, 0, 0, 0)
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
