"""A GTK display for the tests that build windows (Xvfb: tests/run.py --gui runs them under
xvfb-run), with the desktop's animations off so results show at once (the animation tests turn
them on), the one application every window test shares, a stand-in for gtk4-layer-shell, and
screens of any size.

Without GTK or a display nc is None and SKIP says why, or with NOWCAPTION_REQUIRE_GUI_TESTS=1 the
import fails. No models or audio pipeline are started.
"""
import contextlib
import os
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from support.gtk import SKIP, nc

if nc:
    try:
        from gi.repository import Gdk
        if not nc.Gtk.init_check() or Gdk.Display.get_default() is None:
            raise RuntimeError('No GTK display available; run with xvfb-run')
    except (ImportError, RuntimeError) as e:
        if os.environ.get('NOWCAPTION_REQUIRE_GUI_TESTS') == '1':
            raise
        nc, SKIP = None, str(e)
if nc:  # results show at once, except in the animation tests
    nc.Gtk.Settings.get_default().set_property('gtk-enable-animations', False)


APP = []


def shared_app():
    """One registered application for every test class: a second one could not export its
    actions on the session bus under the same path."""
    if not APP:
        app = nc.NowCaption()
        # A NowCaption that is already running owns the app ID; as its remote instance this
        # one would have no windows of its own, and GTK crashes building one.
        app.set_flags(app.get_flags() | nc.Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        app.fullscreen_watch.bus = None  # never load a KWin script into the desktop running the tests
        app.panel_float.bus = None  # nor change the panels of the desktop running the tests
        APP.append(app)
    return APP[0]


@contextlib.contextmanager
def screen_size(width, height):
    """Every screen measures width x height to NowCaption while this lasts, whatever the size of
    the test display (xvfb-run's is 640x480)."""
    geometry = Gdk.Rectangle()
    geometry.width, geometry.height = width, height
    with patch.object(Gdk.Monitor, 'get_geometry', lambda monitor: geometry):
        yield


class FakeLayerShell:
    """Records what NowCaption asks of gtk4-layer-shell; the test display has none."""

    class Edge:
        TOP, BOTTOM, LEFT, RIGHT = 'top', 'bottom', 'left', 'right'

    class KeyboardMode:
        NONE, ON_DEMAND = 0, 1

    class Layer:
        TOP, OVERLAY = 2, 3

    def __init__(self):
        self.anchors, self.zone, self.layer = {}, None, None

    def set_layer(self, window, layer):
        self.layer = layer

    def is_supported(self):
        return True

    def set_anchor(self, window, edge, anchored):
        self.anchors[edge] = anchored

    def set_exclusive_zone(self, window, zone):
        self.zone = zone

    def auto_exclusive_zone_enable(self, window):
        self.zone = 'auto'

    def __getattr__(self, name):  # init_for_window, set_layer, set_monitor, ...
        return lambda *args: None
