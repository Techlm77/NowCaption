"""Where the captions go: floating over the screen, or docked along one of its edges like a panel,
where the compositor keeps other windows out of their strip (layer shell). Moving between them,
docked captions slide into their edge and out of the new one; floating ones fade. Docked, they
float over a fullscreen app and touch Plasma's panels, through the app's KWin watches. Without
layer shell the captions always float, and none of this runs."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk

from ...style import ALONG, DOCK_MS, FADE_IN_MS, FADE_OUT_MS, UNDOCK_MS
from . import layer_shell


class Docking:
    """Anchors the window for the chosen position, and moves the captions there. It sets the
    window's edge (None: floating). covered is set while a fullscreen app on the captions' screen
    floats them; moves holds the slides under way, and moving counts the moves begun."""

    def __init__(self, window):
        self.window = window
        self.covered = False  # a fullscreen app on this screen: docked captions float over it
        self.moves, self.moving = [], 0

    def dock(self):
        """Anchor the overlay for the position chosen in the menu. Floating, it covers the screen
        and the box moves inside it. Docked, it is a strip along one edge that the compositor
        keeps other windows out of, so maximised and snapped windows end where it begins."""
        win, shell = self.window, layer_shell.LayerShell
        position = self.position()
        win.edge = None if position == 'floating' else position
        win.span = None
        # docked, like a panel: next to the panels already there; floating, above everything,
        # fullscreen apps included
        shell.set_layer(win, shell.Layer.TOP if win.edge else shell.Layer.OVERLAY)
        self.watch_fullscreen()
        self.touch_panels()
        for name in ('top', 'bottom', 'left', 'right'):
            shell.set_anchor(win, getattr(shell.Edge, name.upper()),
                             win.edge is None or name == win.edge or name in ALONG[win.edge])
        if win.edge:
            shell.auto_exclusive_zone_enable(win)
            win.add_css_class('docked')
        else:
            shell.set_exclusive_zone(win, -1)  # float over everything, panels too
            win.remove_css_class('docked')
        win.seat_footer()
        win.set_cursor(Gdk.Cursor.new_from_name(win.pointer.cursor(), None))

    def position(self):
        """Where the captions go now: the menu's choice, or floating over a fullscreen app."""
        chosen = self.window.settings.get('position')
        return 'floating' if self.covered else chosen

    def watch_fullscreen(self):
        """While the captions are docked, have KWin say when an app on their screen is fullscreen.
        Once it stops, what it said no longer holds: docked again later, the captions would float
        over an app that may have closed meanwhile, until the new watch said otherwise."""
        win = self.window
        if win.settings.get('position') == 'floating':
            win.app.fullscreen_watch.stop()
            self.covered = False
        elif win.monitor:
            g = win.monitor.get_geometry()
            win.app.fullscreen_watch.start(win.monitor.get_connector() or '', (g.x, g.y, g.width, g.height))
        else:
            win.app.fullscreen_watch.start('')

    def touch_panels(self):
        """Docked, the captions touch the panels on their edge: on Plasma those stop floating, as
        they do for a window that touches them (see PANELS_SCRIPT). Floating over a fullscreen
        app changes nothing: the panels are hidden then, and the captions dock again after."""
        win = self.window
        chosen = win.settings.get('position')
        output = (win.monitor.get_connector() or '') if win.monitor else ''
        win.app.panel_float.dock(None if chosen == 'floating' else chosen, output)

    def set_covered(self, covered):
        """A fullscreen app on this screen opened or closed: float over it, or dock again."""
        if covered != self.covered and self.window.layer:
            self.covered = covered
            self.reposition()

    def reposition(self):
        """Float or dock as the menu now says. Docked captions slide into their edge while the
        space they reserve shrinks with them, so maximised windows grow along (each app redraws
        as fast as it can and skips the sizes it misses); then they slide out of the new edge
        the same way. Floating captions fade instead. An open menu moves with them."""
        win = self.window
        if not win.layer:
            return
        self.moving += 1
        token = self.moving
        for animation in self.moves:
            animation.pause()  # a move already under way stops where it is
        self.moves = []
        if not (win.get_mapped() and Adw.get_enable_animations(win)):
            win.box.set_opacity(1.0)
            return self.arrive(slide_in=False)

        def arrived():
            if token != self.moving:
                return
            self.arrive(slide_in=True)
            if win.edge:
                win.box.set_opacity(1.0)
                self.slide(0, win.drawer.full(), DOCK_MS, Adw.Easing.EASE_OUT_CUBIC, self.docked_fully)
            else:
                win.fade(win.box, 0.0, 1.0, FADE_IN_MS)

        if win.edge:
            win.drawer.edge = win.edge
            shown = win.drawer.full() if win.drawer.shown is None else win.drawer.shown
            self.slide(shown, 0, UNDOCK_MS, Adw.Easing.EASE_IN_CUBIC, arrived)
        else:
            win.fade(win.box, None, 0.0, FADE_OUT_MS, arrived)

    def docked_fully(self):
        """The slide out of the edge is over: the strip reserves its whole size again."""
        win = self.window
        win.drawer.show_part(None, None)
        layer_shell.LayerShell.auto_exclusive_zone_enable(win)
        win.update_input()

    def arrive(self, slide_in):
        """Anchor and fit the captions at the new position; docked, slid away into the edge
        first when they are about to slide out of it."""
        win = self.window
        self.dock()
        if win.edge and slide_in:
            win.drawer.show_part(win.edge, 0)
            layer_shell.LayerShell.set_exclusive_zone(win, 0)
        else:
            win.drawer.show_part(None, None)
        win.restyle()
        if win.menu.popover.get_visible():
            win.menu.point_menu(win.x + win.box_width // 2, win.y + win.box_height() // 2)

    def slide(self, start, end, ms, easing, done):
        """Show start..end px of the docked box over ms. Each frame the space the captions
        reserve, and the part that takes clicks, is just what shows, so maximised windows move
        with the slide; the window itself keeps its size."""
        win = self.window

        def step(value):
            win.drawer.show_part(win.edge, value)
            layer_shell.LayerShell.set_exclusive_zone(win, round(value))
            win.update_input()
        animation = Adw.TimedAnimation.new(win, start, end, ms, Adw.CallbackAnimationTarget.new(step))
        animation.set_easing(easing)
        animation.connect('done', lambda *a: done())
        self.moves.append(animation)
        animation.play()

    def docked_part(self, w, h):
        """The part of a docked window the captions show: all of it, or during a slide only
        what has slid out of the edge."""
        away = self.window.drawer.hidden()
        return {'top': (0, 0, w, h - away), 'bottom': (0, away, w, h - away),
                'left': (0, 0, w - away, h), 'right': (away, 0, w - away, h)}[self.window.edge]
