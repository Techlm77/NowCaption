"""gtk4-layer-shell, where the compositor has it (KDE Plasma, Hyprland, Sway, ...): it lets the
captions' window cover a screen, or dock along its edge like a panel. LayerShell is None without
it. The caption window's parts look it up here each time (layer_shell.LayerShell), so this is the
one place to replace it, as the tests do (with on_wayland, since their display is X11)."""
from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gdk

try:
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None


def on_wayland() -> bool:
    """Whether GTK's display is a Wayland one, the only kind that can have layer shell."""
    display = Gdk.Display.get_default()
    return display is not None and type(display).__gtype__.name == 'GdkWaylandDisplay'


def supported() -> bool:
    """Whether the captions can place their own window: elsewhere the window manager does. Asked
    on X11, gtk4-layer-shell logs a critical warning (GDK_IS_WAYLAND_DISPLAY failed), so it is
    asked only on Wayland."""
    return bool(LayerShell and on_wayland() and LayerShell.is_supported())
