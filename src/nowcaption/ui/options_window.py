"""More options: the window for what the caption box's menu has no room for, with a sample caption
(Preview, in preview.py) that shows how new words arrive."""
import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ..desktop import portal  # noqa: E402
from ..settings import APP_ID  # noqa: E402
from ..style import WORD_EFFECTS, WORD_SPEEDS, css_font  # noqa: E402
from .preview import Preview  # noqa: E402
from .weak import weakly  # noqa: E402


class OptionsWindow(Adw.Window):
    """More options, for what the caption box's menu has no room for: what to listen to, the
    font and how new words arrive (with a sample caption showing them), how long transcripts are
    kept, the word list, starting at login and keyboard shortcuts. Choices show in the sample first
    and reach the captions only with Apply; closing without it drops them. The word list, starting
    at login and the shortcuts are not choices to compare, so they apply at once."""

    SETTINGS = {'source': 'source', 'word-effect': 'word_effect', 'word-speed': 'word_speed',
                'history-days': 'history_days', 'font': 'font'}  # action: key
    HISTORY = {1: '1 day', 7: '7 days', 30: '30 days', 90: '90 days', 0: 'Until you delete them'}

    def __init__(self, app):
        super().__init__(application=app, title='More options', default_width=560, default_height=760)
        self.app = app
        self.chosen = Gio.SimpleActionGroup()  # the choices made here, until Apply
        for name, key in self.SETTINGS.items():
            action = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new('s'),
                                                   GLib.Variant('s', str(app.settings.get(key))))
            action.connect('activate', weakly(self.on_choice))
            self.chosen.add_action(action)
        self.sample_css = Gtk.CssProvider()  # the sample's font, chosen here but not applied yet
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.sample_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.connect('close-request', weakly(self.on_close))
        self.quiet = False  # set while a control is set from here, not by the user
        self.insert_action_group('options', self.chosen)
        page = Adw.PreferencesPage()
        listen = Adw.PreferencesGroup(title='Listen to')
        self.choices(listen, 'options.source', (('desktop', 'Desktop audio', 'Everything your computer plays'),
                                                ('mic', 'Microphone', 'Your microphone, for the people around you')))
        # an action, not a choice: it listens again at once, without Apply
        restart = Gtk.Button(label='Restart', action_name='app.restart-listening', valign=Gtk.Align.CENTER)
        again = Adw.ActionRow(title='Start again', subtitle='After a problem, or when you change sound devices',
                              activatable_widget=restart)
        again.add_suffix(restart)
        listen.add(again)
        page.add(listen)
        page.add(self.font_group())
        self.preview = Preview(app)
        self.preview.add_css_class('options-sample')
        sample = Adw.PreferencesGroup(title='New words', description='How each new word appears in the captions')
        sample.add(self.preview)
        page.add(sample)
        words = Adw.PreferencesGroup()
        self.choices(words, 'options.word-effect', ((name, title, subtitle)
                                                   for name, (title, subtitle) in WORD_EFFECTS.items()))
        speeds = Gtk.Box(css_classes=['linked'], valign=Gtk.Align.CENTER)
        for name, (label, _) in WORD_SPEEDS.items():
            speeds.append(Gtk.ToggleButton(label=label, action_name='options.word-speed',
                                           action_target=GLib.Variant('s', name)))
        self.speed = Adw.ActionRow(title='Speed')
        self.speed.add_suffix(speeds)
        words.add(self.speed)
        page.add(words)
        motion = Adw.PreferencesGroup(description='With animations turned off for your desktop, new words '
                                                  'appear at once, whichever way is chosen.')
        page.add(motion)
        page.add(self.words_group())
        self.fixes = Adw.PreferencesGroup()  # the word list's fixes, one row each
        self.fix_rows = []
        page.add(self.fixes)
        history = Adw.PreferencesGroup(title='Keep transcripts for',
                                       description='Finished sessions stay in the transcript this long. A shorter '
                                                   'time deletes the older ones when you press Apply.')
        self.choices(history, 'options.history-days', ((str(days), title, None) for days, title in self.HISTORY.items()))
        page.add(history)
        page.add(self.start_group())
        self.apply_button = Gtk.Button(label='Apply', css_classes=['suggested-action'],
                                       tooltip_text='Use these choices for the captions')
        self.apply_button.connect('clicked', weakly(self.apply))
        header = Adw.HeaderBar()
        header.pack_end(self.apply_button)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self.set_content(view)
        close = Gtk.ShortcutController()
        close.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string('Escape'),
                                            Gtk.NamedAction.new('window.close')))
        self.add_controller(close)
        self.show_choices()
        self.show_fixes()
        self.show_shortcuts()

    def on_close(self, window):
        Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self.sample_css)
        return False

    def choice(self, name):
        return self.chosen.lookup_action(name).get_state().get_string()

    def changed(self):
        """The choices that differ from what the captions use."""
        return [name for name, key in self.SETTINGS.items() if self.choice(name) != str(self.app.settings.get(key))]

    # --- font -------------------------------------------------------------------------------

    @staticmethod
    def desktop_font():
        return Pango.FontDescription.from_string(Gtk.Settings.get_default().get_property('gtk-font-name') or 'Sans')

    def font_group(self):
        """The caption font: any installed family, or the desktop's own (the default)."""
        group = Adw.PreferencesGroup(title='Font')
        self.font_button = Gtk.FontDialogButton(dialog=Gtk.FontDialog(title='Caption font'), level=Gtk.FontLevel.FAMILY,
                                                use_font=True, valign=Gtk.Align.CENTER)
        family = self.choice('font')
        self.font_button.set_font_desc(Pango.FontDescription.from_string(family) if family else self.desktop_font())
        self.font_button.connect('notify::font-desc', weakly(self.on_font_picked))
        self.font_reset = Gtk.Button(icon_name='edit-undo-symbolic', tooltip_text='Use the desktop\'s font',
                                     valign=Gtk.Align.CENTER, css_classes=['flat'])
        self.font_reset.update_property([Gtk.AccessibleProperty.LABEL], ['Use the desktop\'s font'])
        self.font_reset.connect('clicked', weakly(self.on_font_reset))
        self.font_row = Adw.ActionRow(title='Caption font', activatable_widget=self.font_button)
        self.font_row.add_suffix(self.font_reset)
        self.font_row.add_suffix(self.font_button)
        group.add(self.font_row)
        return group

    def on_font_picked(self, button, pspec):
        desc = button.get_font_desc()
        if not self.quiet and desc and desc.get_family():
            self.chosen.lookup_action('font').activate(GLib.Variant('s', desc.get_family()))

    def on_font_reset(self, button):
        self.quiet = True
        self.font_button.set_font_desc(self.desktop_font())
        self.quiet = False
        self.chosen.lookup_action('font').activate(GLib.Variant('s', ''))

    def show_font(self):
        family = self.choice('font')
        self.font_row.set_subtitle(family or 'Your desktop\'s font')
        self.font_reset.set_visible(bool(family))
        self.sample_css.load_from_string(f'.caption-preview.options-sample .caption-text {{ font-family: '
                                         f'{css_font(family) if family else "inherit"}; }}')

    # --- word list --------------------------------------------------------------------------

    def words_group(self):
        """Adding a fix: what NowCaption writes for a word it gets wrong, and what to show."""
        group = Adw.PreferencesGroup(
            title='Word fixes',
            description='For names and words NowCaption gets wrong, such as Nvidia written as “in video”. '
                        'Leave the first box empty to only fix how a word is spelled, such as NVIDIA. '
                        'Fixes apply at once, to everything heard from then on.')
        self.heard_entry = Adw.EntryRow(title='When NowCaption writes')
        self.write_entry = Adw.EntryRow(title='Show instead')
        add = Gtk.Button(label='Add', valign=Gtk.Align.CENTER, css_classes=['suggested-action'])
        add.connect('clicked', weakly(self.add_fix))
        self.write_entry.add_suffix(add)
        for entry in (self.heard_entry, self.write_entry):
            entry.connect('entry-activated', weakly(self.add_fix))
        group.add(self.heard_entry)
        group.add(self.write_entry)
        return group

    def add_fix(self, *args):
        write = ' '.join(self.write_entry.get_text().split())
        heard = ' '.join(self.heard_entry.get_text().split())
        if not write:
            self.write_entry.grab_focus()
            return
        fixes = [f for f in self.app.word_list if f[0].lower() != heard.lower() or (not heard and f[1] != write)]
        self.save_fixes(fixes + [(heard, write)])
        self.heard_entry.set_text('')
        self.write_entry.set_text('')
        self.heard_entry.grab_focus()

    def remove_fix(self, fix):
        self.save_fixes([f for f in self.app.word_list if f != fix])

    def save_fixes(self, fixes):
        error = self.app.set_words(fixes)
        self.show_fixes()
        if error:
            self.fixes.set_description(error)

    def show_fixes(self):
        for row in self.fix_rows:
            self.fixes.remove(row)
        self.fix_rows = []
        self.fixes.set_description(None if self.app.word_list else 'No fixes yet.')
        for heard, write in self.app.word_list:
            row = Adw.ActionRow(title=GLib.markup_escape_text(write),
                                subtitle=GLib.markup_escape_text(f'instead of “{heard}”' if heard else 'spelling only'))
            remove = Gtk.Button(icon_name='user-trash-symbolic', tooltip_text='Remove this fix',
                                valign=Gtk.Align.CENTER, css_classes=['flat'])
            remove.update_property([Gtk.AccessibleProperty.LABEL], [f'Remove the fix for {write}'])
            remove.connect('clicked', weakly(self.on_remove_fix), (heard, write))
            row.add_suffix(remove)
            self.fixes.add(row)
            self.fix_rows.append(row)

    def on_remove_fix(self, button, fix):
        self.remove_fix(fix)

    # --- start at login and keyboard shortcuts ----------------------------------------------

    def start_group(self):
        command = self.launch_command()
        group = Adw.PreferencesGroup(
            title='Start and shortcuts',
            description=f'Any desktop can also run these from a shortcut of its own: “{command} --toggle” '
                        f'shows or hides the captions, “{command} --pause” pauses them.')
        self.login_row = Adw.SwitchRow(title='Start when you log in', subtitle='Captions are ready straight away')
        self.login_row.set_active(self.app.starts_at_login())
        self.login_row.connect('notify::active', weakly(self.on_login))
        group.add(self.login_row)
        self.keys_row = Adw.SwitchRow(title='Keyboard shortcuts')
        self.keys_row.set_active(self.app.settings.get('shortcuts'))
        self.keys_row.connect('notify::active', weakly(self.on_keys))
        group.add(self.keys_row)
        change = Gtk.Button(label='Change…', valign=Gtk.Align.CENTER)
        change.connect('clicked', weakly(self.on_change_keys))
        self.change_row = Adw.ActionRow(title='Change the keys', subtitle='In your desktop\'s settings',
                                        activatable_widget=change)
        self.change_row.add_suffix(change)
        group.add(self.change_row)
        return group

    @staticmethod
    def launch_command():
        """What a desktop shortcut runs to reach this NowCaption: the Flatpak, the AppImage
        (it has no command of its own) or the installed `nowcaption`."""
        if portal.in_flatpak():
            return f'flatpak run {APP_ID}'
        return os.environ.get('APPIMAGE') or 'nowcaption'

    def on_login(self, row, pspec):
        if self.quiet:
            return
        row.set_sensitive(False)  # until the desktop answers
        self.app.set_autostart(row.get_active(), weakly(self.login_answered))

    def login_answered(self, on, message):
        self.quiet = True
        self.login_row.set_active(on)
        self.quiet = False
        self.login_row.set_sensitive(True)
        self.login_row.set_subtitle(message or 'Captions are ready straight away')

    def on_keys(self, row, pspec):
        if not self.quiet:
            self.app.set_shortcuts(row.get_active())

    def on_change_keys(self, button):
        if self.app.shortcuts:
            self.app.shortcuts.configure()

    def show_shortcuts(self):
        keys = self.app.shortcuts
        on = self.app.settings.get('shortcuts')
        if not on:
            text = 'Show or hide and pause the captions from any app'
        elif not keys:
            text = 'Not available: NowCaption is not on a session bus'
        elif keys.error and not keys.bound:
            text = keys.error
        elif keys.bound:
            names = dict((sid, text) for sid, text, _ in keys.SHORTCUTS)
            text = ' · '.join(f'{keys.bound.get(sid) or "No key"}: {names[sid].lower()}' for sid in names)
        else:
            text = 'Asking your desktop…'
        self.keys_row.set_subtitle(GLib.markup_escape_text(text))
        self.change_row.set_visible(bool(on and keys and keys.available and keys.can_configure))

    def on_choice(self, action, value):
        action.set_state(value)
        self.show_choices()

    def show_choices(self):
        self.show_font()
        effect = self.choice('word-effect')
        self.preview.caption.set_effect(effect, WORD_SPEEDS[self.choice('word-speed')][1])
        self.speed.set_sensitive(effect != 'none')  # no speed for words that appear at once
        self.apply_button.set_sensitive(bool(self.changed()))

    def apply(self, button=None):
        for name in self.changed():  # only those, so the same source does not restart capture
            self.app.lookup_action(name).activate(GLib.Variant('s', self.choice(name)))
        self.show_choices()

    @staticmethod
    def choices(group, action, options):
        """A row for each (value, title, subtitle), chosen with a radio button bound to action."""
        first = None
        for value, title, subtitle in options:
            check = Gtk.CheckButton(action_name=action, action_target=GLib.Variant('s', value),
                                    valign=Gtk.Align.CENTER)
            if first:
                check.set_group(first)
            first = first or check
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable_widget=check)
            row.add_prefix(check)
            group.add(row)
