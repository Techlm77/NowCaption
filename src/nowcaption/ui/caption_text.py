"""Drawing the caption passage: the caption text widget that glides the rows up and brings new
words in, and the label that says when GTK has restyled it. The rows' wrapping is in rows.py,
the pixel effects' pieces in pieces.py. Nothing here knows about windows or settings: the caption
window and the sample caption configure it."""
import difflib
import logging
import math
import re

import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, GLib, Graphene, Gsk, Gtk, Pango

from ..style import BREATH_MS, BREATH_STEP_MS, GLIDE_MS, PIXEL_MS, PIXEL_WORDS, ROWS, STATUS_MS, WORD_MS
from .pieces import Pieces
from .rows import Rows, bare

LOG = logging.getLogger('nowcaption')


def ease_out(p):
    return 1 - (1 - p) ** 3


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

        def line(row):
            return ' '.join(bare(w) for w in row.split())
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
