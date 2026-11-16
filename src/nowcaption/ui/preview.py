"""The sample caption under More options, in the captions' own look, that keeps adding words
like speech so each way for new words to arrive can be seen. The caption box's stylesheet
styles it too (.caption-preview), up to Preview.MAX_SIZE."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..style import PAD_X, PAD_Y  # noqa: E402
from .caption_text import CaptionText, StyledLabel  # noqa: E402
from .weak import weakly  # noqa: E402


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
