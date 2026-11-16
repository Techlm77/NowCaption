"""The pixel effects' pieces: a new word cut into small squares that fly, drop or switch on into
place, drawn with cairo and numpy. CaptionText decides when each word arrives."""
import math

import cairo
import gi
import numpy as np
gi.require_version('Gtk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Graphene, Gtk  # noqa: E402

from ..style import MOSAIC  # noqa: E402


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
