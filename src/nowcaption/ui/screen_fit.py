"""A window's first height, kept within its screen: a height that suits a desktop monitor runs off
a laptop's 1366x768 or 1280x720 screen. The windows that use it scroll their content, so any
height fits. No widgets of its own."""
from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

if TYPE_CHECKING:  # for annotations only
    from gi.repository import Gdk

# The most of its screen's height a window opens with. On Wayland GTK sees only the whole screen,
# not the panels on it, so the rest is left for them: GNOME's top bar, a Plasma panel or a dock.
SCREEN_SHARE = 0.85


def fit_height(window: Gtk.Window, height: int) -> None:
    """Have window open height tall, or SCREEN_SHARE of its screen's height on a screen too short
    for that, so its bottom never runs off the screen. Only its first height: the window can
    still be made taller by hand."""
    window.set_property('default-height', height)
    window.connect('realize', on_realize, height)


def on_realize(window, height):
    """The window has a surface: fit it to the screen GTK expects it on. Until the desktop shows
    the window, GTK can only guess which screen that is, so fit it again once it is shown."""
    surface = window.get_surface()
    fit(window, window.get_display().get_monitor_at_surface(surface), height)
    surface.connect('enter-monitor', on_enter_monitor, height)


def on_enter_monitor(surface, monitor, height):
    """The window is shown on its first screen: fit it to that one. Only the first, so a window
    the user has moved to another screen keeps its size."""
    surface.disconnect_by_func(on_enter_monitor)
    window = Gtk.Native.get_for_surface(surface)
    if window:
        fit(window, monitor, height)


def fit(window: Gtk.Window, monitor: Gdk.Monitor | None, height: int) -> None:
    """Make window's default height height, or SCREEN_SHARE of monitor's height if that is less."""
    if monitor:
        height = min(height, int(monitor.get_geometry().height * SCREEN_SHARE))
    window.set_property('default-height', height)
