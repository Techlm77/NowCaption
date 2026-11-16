"""The two containers that hold the caption box in its window: Stage places the floating box at
x, y inside an overlay covering the screen; Drawer shows only part of a docked box, the rest
slid away into its edge. Layout only: they know nothing of captions or settings."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Graphene, Gsk, Gtk  # noqa: E402


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
    Docking shrinks and grows only the space it reserves. shown None: all of it."""

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
