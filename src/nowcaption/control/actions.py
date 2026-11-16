"""The app's actions (app.colour, app.pause, app.quit…), each handled by the collaborator whose job it
is, their keyboard shortcuts, and the caption box's menu that offers them. Never imports the
windows (ui) or app.py."""
from gi.repository import Gio, GLib

from .. import engine
from ..style import STEPPERS


def add_actions(app):
    """Add every action to app. Returns the keyboard accelerators (action -> keys), which are
    registered once we know the window can take keyboard focus."""
    accels = {}

    def act(name, fn, keys=()):
        a = Gio.SimpleAction.new(name, None)
        a.connect('activate', lambda *_: fn())
        app.add_action(a)
        if keys:
            accels['app.' + name] = list(keys)
    settings, appearance, history, listening = app.settings, app.appearance, app.history, app.listening
    source = Gio.SimpleAction.new_stateful('source', GLib.VariantType.new('s'),
                                           GLib.Variant('s', settings.get('source')))
    source.connect('activate', listening.on_source)
    app.add_action(source)
    colour = Gio.SimpleAction.new_stateful('colour', GLib.VariantType.new('s'),
                                           GLib.Variant('s', settings.get('colour')))
    colour.connect('activate', appearance.on_colour)
    app.add_action(colour)
    act('larger', lambda: appearance.resize(+1), ['<Control>plus', '<Control>equal'])
    act('smaller', lambda: appearance.resize(-1), ['<Control>minus'])
    act('lighter', lambda: appearance.opacity(-0.15))
    act('darker', lambda: appearance.opacity(+0.15))
    position = Gio.SimpleAction.new_stateful('position', GLib.VariantType.new('s'),
                                             GLib.Variant('s', settings.get('position')))
    position.connect('activate', appearance.on_position)
    app.add_action(position)
    for name, key, handler in (('word-effect', 'word_effect', appearance.on_word_effect),
                               ('word-speed', 'word_speed', appearance.on_word_speed),
                               ('history-days', 'history_days', history.on_history_days),
                               ('font', 'font', appearance.on_font)):
        action = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new('s'),
                                               GLib.Variant('s', str(settings.get(key))))
        action.connect('activate', handler)
        app.add_action(action)
    history_on = Gio.SimpleAction.new_stateful('history', None, GLib.Variant('b', settings.get('history')))
    history_on.connect('activate', history.on_history)
    app.add_action(history_on)
    pause = Gio.SimpleAction.new_stateful('pause', None, GLib.Variant('b', False))
    pause.connect('activate', lambda action, value: listening.set_paused(not listening.paused))
    app.add_action(pause)
    act('toggle', app.toggle_captions)
    act('transcript', app.open_transcript, ['<Control>t'])
    act('options', app.open_options, ['<Control>comma'])
    act('restart-listening', listening.restart_listening)
    act('reset', lambda: (app.window.reset_position(), app.window.reset_size()))
    act('reset-position', lambda: app.window.reset_position())
    act('reset-size', lambda: app.window.reset_size())
    act('next-screen', lambda: app.window.next_monitor())
    act('copy', app.saving.copy_transcript)
    act('save', app.saving.save_transcript, ['<Control>s'])
    act('clear', listening.clear_transcript)
    act('about', app.about)
    act('quit', app.quitting.request_quit, ['<Control>q'])
    return accels


def build_menu(history_days, several_screens=False, layer=False):
    """The caption box's menu, in sections: how captions look, the box's place, the
    transcript, then More options (a window for the rest) and the app. CaptionWindow.update_menu
    fills the 'custom' items with rows of buttons that keep the menu open. history_days is how
    long sessions are kept."""
    def section(label, *items):
        m = Gio.Menu()
        for item in items:
            m.append_item(item)
        return Gio.MenuItem.new_section(label, m)

    def custom(name):
        item = Gio.MenuItem.new(None, None)
        item.set_attribute_value('custom', GLib.Variant('s', name))
        return item

    item = Gio.MenuItem.new
    menu = Gio.Menu()
    menu.append_item(section('Appearance', *(custom(name) for name in (*STEPPERS, 'text-colour'))))
    place = [item('Reset position and size', 'app.reset')]
    if layer:  # elsewhere the window manager places the window
        place.insert(0, custom('position'))
    if several_screens:
        place.append(item('Move to next screen', 'app.next-screen'))
    menu.append_item(section(None, *place))
    span = engine.history_span(history_days)
    menu.append_item(section('Transcript', item('Open transcript', 'app.transcript'),
                             item(f'Keep {span} of history' if span else 'Keep history', 'app.history'),
                             item('Clear this session', 'app.clear')))
    menu.append_item(section(None, item('Pause captions', 'app.pause'), item('More options…', 'app.options'),
                             item('About NowCaption', 'app.about'), item('Quit', 'app.quit')))
    return menu
