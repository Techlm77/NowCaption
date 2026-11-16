"""Wrapping the caption passage into rows that keep their breaks, so words do not hop between
rows as the passage grows or is punctuated anew. Pango layouts only: no widgets."""
import bisect
from itertools import pairwise

import gi

gi.require_version('Pango', '1.0')
from gi.repository import Pango


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
        return [(a + s, a + e) for s, e in pairwise(bounds) if s < e]

    def fits(self, row):
        self.fit.set_text(' '.join(row), -1)
        return self.fit.get_size()[0] <= self.width
