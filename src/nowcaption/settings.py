"""The app's ID and its settings: what each one may be, its default, and the key file that keeps them.
Imports GLib, whose key file is the settings file's format and whose config folder is the
Flatpak's own, but never GTK, the windows or app.py."""
from __future__ import annotations

import logging
from pathlib import Path

from gi.repository import GLib

from . import engine
from .style import COLOURS, MIN_WIDTH, POSITIONS, ROWS, SIZES, WORD_EFFECTS, WORD_SPEEDS

APP_ID = 'io.github.Techlm77.NowCaption'
LOG = logging.getLogger('nowcaption')
DEFAULTS = {'source': 'desktop', 'size': 30, 'opacity': 0.78, 'x': -1, 'y': -1, 'monitor': '',
            'width': -1, 'rows': ROWS, 'colour': 'white', 'history': True,
            'position': 'floating', 'dock_rows': ROWS, 'dock_width': -1,
            'word_effect': 'fade', 'word_speed': 'normal', 'unfloated': '',
            'history_days': engine.HISTORY_DAYS, 'font': '', 'shortcuts': False, 'autostart': False}
LIMITS = {'size': (SIZES[0], SIZES[-1]), 'opacity': (0.2, 1.0), 'rows': (1, 100), 'dock_rows': (1, 100)}
CHOICES = {'source': ('desktop', 'mic'), 'colour': tuple(COLOURS), 'position': tuple(POSITIONS),
           'word_effect': tuple(WORD_EFFECTS), 'word_speed': tuple(WORD_SPEEDS),
           'history_days': engine.HISTORY_CHOICES}


class Settings:
    """Small key file in the user's config directory."""

    def __init__(self) -> None:
        self.path = Path(GLib.get_user_config_dir()) / 'nowcaption' / 'settings.ini'
        self.kf = GLib.KeyFile()
        try:
            self.kf.load_from_file(str(self.path), GLib.KeyFileFlags.NONE)
        except GLib.Error:
            pass

    def get(self, key: str) -> bool | int | float | str:
        """The stored value, brought back into range if the file was edited by hand."""
        default = DEFAULTS[key]
        try:
            if isinstance(default, bool):
                value = self.kf.get_boolean('main', key)
            elif isinstance(default, int):
                value = self.kf.get_integer('main', key)
            elif isinstance(default, float):
                value = self.kf.get_double('main', key)
            else:
                value = self.kf.get_string('main', key)
        except GLib.Error:
            return default
        if key in LIMITS:
            low, high = LIMITS[key]
            value = max(low, min(high, value))
        if key == 'width' and 0 < value < MIN_WIDTH:
            value = MIN_WIDTH
        if key in CHOICES and value not in CHOICES[key]:
            value = default
        return value

    def set(self, key: str, value: bool | int | float | str) -> None:
        """Remember value; if the file cannot be written it still applies until the app quits."""
        {bool: self.kf.set_boolean, int: self.kf.set_integer,
         float: self.kf.set_double}.get(type(DEFAULTS[key]), self.kf.set_string)('main', key, value)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.kf.save_to_file(str(self.path))
        except (OSError, GLib.Error) as e:
            LOG.warning('Could not save settings to %s: %s', self.path, e)
