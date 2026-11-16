"""gtk4-layer-shell, where the compositor has it (KDE Plasma, Hyprland, Sway, ...): it lets the
captions' window cover a screen, or dock along its edge like a panel. LayerShell is None without
it. The caption window's parts look it up here each time (layer_shell.LayerShell), so this is the
one place to replace it, as the tests do."""
import gi

gi.require_version('Gtk', '4.0')

try:
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None


def supported():
    """Whether the captions can place their own window: elsewhere the window manager does."""
    return bool(LayerShell and LayerShell.is_supported())
