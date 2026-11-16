"""How the captions look, as the menu and More options change it: text size, background, colour,
font, the box's position and how new words arrive. Keeps each choice in the app's settings and
applies it to its caption window; never imports the windows (ui), and app.py only for annotations."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..style import COLOURS, POSITIONS, SIZES, WORD_EFFECTS, WORD_SPEEDS

if TYPE_CHECKING:  # for annotations only
    from ..app import NowCaption


class Appearance:
    """The actions that change how the captions look (see actions.add_actions)."""

    def __init__(self, app: NowCaption) -> None:
        self.app = app

    def on_colour(self, action, value):
        """The menu's Text colour: use and keep a colour from COLOURS."""
        if value.get_string() not in COLOURS:
            return
        action.set_state(value)
        self.app.settings.set('colour', value.get_string())
        if self.app.window:
            self.app.window.restyle()

    def resize(self, step: int) -> None:
        """Make the text a size larger (step 1) or smaller (-1), from those in SIZES."""
        size = self.app.settings.get('size')
        i = min(range(len(SIZES)), key=lambda k: abs(SIZES[k] - size))
        self.app.settings.set('size', SIZES[max(0, min(len(SIZES) - 1, i + step))])
        self.app.window.restyle()

    def opacity(self, step: float) -> None:
        """Make the background more solid (a positive step) or more see-through, from 0.2 to 1."""
        self.app.settings.set('opacity', round(max(0.2, min(1.0, self.app.settings.get('opacity') + step)), 2))
        self.app.window.restyle()

    def on_font(self, action, value):
        """More options' font: use and keep a family name, or '' for the desktop's own."""
        action.set_state(value)
        self.app.settings.set('font', ' '.join(value.get_string().split()))
        if self.app.window:
            self.app.window.restyle()

    def on_position(self, action, value):
        """The menu's Position (POSITIONS): float the box, or dock it on an edge of the screen."""
        if value.get_string() not in POSITIONS:
            return
        action.set_state(value)
        self.app.settings.set('position', value.get_string())
        if self.app.window:
            self.app.window.reposition()

    def on_word_effect(self, action, value):
        """More options' word effect (WORD_EFFECTS): how new words arrive."""
        if value.get_string() in WORD_EFFECTS:
            action.set_state(value)
            self.app.settings.set('word_effect', value.get_string())
            self.show_word_effect()

    def on_word_speed(self, action, value):
        """More options' word speed (WORD_SPEEDS): how fast the word effect plays."""
        if value.get_string() in WORD_SPEEDS:
            action.set_state(value)
            self.app.settings.set('word_speed', value.get_string())
            self.show_word_effect()

    def word_effect(self) -> tuple[str, float]:
        """How new words arrive, and the factor of its speed, as CaptionText.set_effect takes them."""
        return self.app.settings.get('word_effect'), WORD_SPEEDS[self.app.settings.get('word_speed')][1]

    def show_word_effect(self) -> None:
        """Give the caption box's text the chosen word effect and speed."""
        if self.app.window:
            self.app.window.caption.set_effect(*self.word_effect())
