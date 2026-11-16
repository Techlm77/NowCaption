"""Desktop integration: the XDG desktop portals (keys that work in any app, starting at login) and
KDE Plasma (fullscreen apps, floating panels), all over D-Bus.
Uses nothing else in NowCaption, but names Settings in annotations (CONTRIBUTING.md)."""
from .autostart import Autostart, desktop_exec, launch_command
from .plasma import FULLSCREEN_IFACE, FULLSCREEN_PATH, FULLSCREEN_XML, FullscreenWatch, PanelFloat
from .portal import Portal, in_flatpak, portal_for
from .shortcuts import GlobalShortcuts

__all__ = ['Autostart', 'desktop_exec', 'launch_command',
           'FULLSCREEN_IFACE', 'FULLSCREEN_PATH', 'FULLSCREEN_XML', 'FullscreenWatch', 'PanelFloat',
           'Portal', 'in_flatpak', 'portal_for', 'GlobalShortcuts']
