"""How the captions look, as the menu and More options change it: text size, background, colour,
font, the box's position and how new words arrive. Keeps each choice in the app's settings and
applies it to its caption window; never imports the windows (ui) or app.py."""
from ..style import COLOURS, POSITIONS, SIZES, WORD_EFFECTS, WORD_SPEEDS


class Appearance:
    """The actions that change how the captions look (see actions.add_actions)."""

    def __init__(self, app):
        self.app = app

    def on_colour(self, action, value):
        if value.get_string() not in COLOURS:
            return
        action.set_state(value)
        self.app.settings.set('colour', value.get_string())
        if self.app.window:
            self.app.window.restyle()

    def resize(self, step):
        size = self.app.settings.get('size')
        i = min(range(len(SIZES)), key=lambda k: abs(SIZES[k] - size))
        self.app.settings.set('size', SIZES[max(0, min(len(SIZES) - 1, i + step))])
        self.app.window.restyle()

    def opacity(self, step):
        self.app.settings.set('opacity', round(max(0.2, min(1.0, self.app.settings.get('opacity') + step)), 2))
        self.app.window.restyle()

    def on_font(self, action, value):
        action.set_state(value)
        self.app.settings.set('font', ' '.join(value.get_string().split()))
        if self.app.window:
            self.app.window.restyle()

    def on_position(self, action, value):
        if value.get_string() not in POSITIONS:
            return
        action.set_state(value)
        self.app.settings.set('position', value.get_string())
        if self.app.window:
            self.app.window.reposition()

    def on_word_effect(self, action, value):
        if value.get_string() in WORD_EFFECTS:
            action.set_state(value)
            self.app.settings.set('word_effect', value.get_string())
            self.show_word_effect()

    def on_word_speed(self, action, value):
        if value.get_string() in WORD_SPEEDS:
            action.set_state(value)
            self.app.settings.set('word_speed', value.get_string())
            self.show_word_effect()

    def word_effect(self):
        """How new words arrive, and the factor of its speed, as CaptionText.set_effect takes them."""
        return self.app.settings.get('word_effect'), WORD_SPEEDS[self.app.settings.get('word_speed')][1]

    def show_word_effect(self):
        if self.app.window:
            self.app.window.caption.set_effect(*self.word_effect())
