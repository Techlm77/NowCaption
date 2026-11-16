"""NowCaption: on-device live captions for anything your computer plays, or your microphone.

The Adw.Application: its lifecycle, its windows and the wiring between them, the desktop (desktop/)
and the collaborators that do the work (control/). Nothing imports this module but the launcher
(control/ names NowCaption in annotations only)."""
from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable, Iterable

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from . import __version__, engine, words
from .control import Appearance, History, Listening, Quitting, Saving, actions, commands, quit_on_signals
from .desktop import (
    FULLSCREEN_PATH,
    FULLSCREEN_XML,
    Autostart,
    FullscreenWatch,
    GlobalShortcuts,
    PanelFloat,
    in_flatpak,
    portal_for,
)
from .settings import APP_ID, Settings
from .ui import CaptionWindow, OptionsWindow, TranscriptWindow

LOG = logging.getLogger(__name__)


class NowCaption(Adw.Application):
    """Owns the windows, the desktop integration and the collaborators in control/, and wires
    them together: listening (the recognition worker and the capture), history and saving (the
    transcript), appearance, quitting, and the actions and launch options that reach them. All
    methods run on the GTK thread."""

    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        commands.add_launch_options(self)
        self.settings = Settings()
        self.window = None
        self.closing = False
        self.started = time.time()
        self.transcript = None  # the transcript window, while it is open
        self.options = None  # the More options window, while it is open
        self.fullscreen_watch = FullscreenWatch(APP_ID)
        self.panel_float = PanelFloat(self.settings)
        self.hidden = False  # the captions were hidden (toggle_captions); they keep captioning
        self.word_list = words.load_words()  # [(heard, write)], edited under More options
        self.shortcuts = self.autostart = None  # see portals(), once on the session bus
        # the collaborators reach the window, the settings and each other through the app
        self.quitting = Quitting(self)
        self.history = History(self)
        self.listening = Listening(self)
        self.saving = Saving(self)
        self.appearance = Appearance(self)
        self.connect('command-line', commands.on_command_line)
        self.connect('activate', self.on_activate)
        self.connect('shutdown', self.on_shutdown)
        self.accels = actions.add_actions(self)  # registered once we know the window can take keyboard focus

    # --- lifecycle --------------------------------------------------------------------------

    def on_activate(self, app):
        """Each start: make the caption box, show it and start listening, or show the box that
        is already there (hidden captions too)."""
        if self.window:
            if self.hidden:  # starting NowCaption again shows hidden captions
                return self.toggle_captions()
            return self.window.present()
        self.window = CaptionWindow(self)
        self.window.connect('close-request', self.quitting.on_window_close)
        if not self.window.layer:  # an overlay never has keyboard focus, so shortcuts would never fire
            for action, keys in self.accels.items():
                self.set_accels_for_action(action, keys)
        self.window.present()
        self.window.show_status('Loading speech model…')
        self.prune_history()
        self.listening.start(self.history.history_path if self.settings.get('history') else None, self.word_list)
        self.portals()
        GLib.timeout_add_seconds(60, self.release_memory)
        shot = os.environ.get('NOWCAPTION_SCREENSHOT')  # "file.png" or "file.png:seconds"
        if shot:
            path, _, after = shot.partition(':')
            GLib.timeout_add_seconds(int(after or 20), self.window.save_png, path)

    def release_memory(self):
        """Runs once a minute: gives freed memory back to the system, and deletes sessions that
        became too old while NowCaption kept running."""
        if self.closing:
            return False
        self.prune_history()  # reads only file names and times: about 10 µs per kept session
        engine.release_free_memory()
        return True

    def on_shutdown(self, app):
        """The app is ending: stop watching for fullscreen apps, let the panels float again, close
        the shortcut session and stop listening."""
        self.closing = True
        self.fullscreen_watch.stop(sync=True)
        self.panel_float.stop()
        if self.shortcuts:
            self.shortcuts.stop()
        self.listening.stop()

    def do_dbus_register(self, connection, object_path):
        """Also take the KWin script's word on fullscreen apps (see FULLSCREEN_PATH)."""
        Adw.Application.do_dbus_register(self, connection, object_path)
        try:
            info = Gio.DBusNodeInfo.new_for_xml(FULLSCREEN_XML).interfaces[0]
            connection.register_object(FULLSCREEN_PATH, info, self.on_fullscreen_call, None, None)
        except GLib.Error as e:  # captions still work, just without floating over fullscreen apps
            LOG.warning('Could not listen for fullscreen apps: %s', e)
        return True

    def on_fullscreen_call(self, connection, sender, path, iface, method, params, invocation):
        """A D-Bus call on FULLSCREEN_PATH: the KWin script's Changed(fullscreen)."""
        invocation.return_value(None)
        if method == 'Changed':
            self.on_fullscreen(params.unpack()[0])

    def on_fullscreen(self, fullscreen: bool) -> None:
        """An app on the captions' screen went fullscreen, or left it: float over it, or dock again."""
        if self.window:
            self.window.set_covered(bool(fullscreen))

    # --- windows ----------------------------------------------------------------------------

    def toggle_captions(self) -> None:
        """Hide the captions, or show them again (they keep captioning while hidden, so the
        transcript has everything). Starting NowCaption again also shows them."""
        window = self.window
        if not window:
            return
        self.hidden = not self.hidden
        if self.hidden:
            window.popover.popdown()
            window.set_visible(False)
            self.panel_float.dock(None)  # a hidden strip does not touch the panels
        else:
            window.present()
            window.touch_panels()

    def open_options(self) -> None:
        """Open More options, or bring it to the front."""
        if self.options is None:
            self.options = OptionsWindow(self)
            self.options.connect('close-request', self.on_options_closed)
        self.options.present()

    def on_options_closed(self, window):
        """More options is closing: the next open_options makes a new one."""
        self.options = None
        return False

    def open_transcript(self) -> None:
        """Open the transcript window, or bring it to the front."""
        if self.transcript is None:
            self.transcript = TranscriptWindow(self)
            self.transcript.connect('close-request', self.on_transcript_closed)
        self.transcript.present()

    def on_transcript_closed(self, window):
        """The transcript window is closing: the next open_transcript makes a new one."""
        self.transcript = None  # the window lets go of the rest itself (TranscriptWindow.on_close)
        return False

    def about(self) -> None:
        """Show the About dialog, with the speech models' licences."""
        about = Adw.AboutDialog(
            application_name='NowCaption', application_icon=APP_ID, version=__version__,
            developer_name='Techlm77', license_type=Gtk.License.MIT_X11,
            website='https://github.com/Techlm77/NowCaption',
            issue_url='https://github.com/Techlm77/NowCaption/issues',
            comments='Live captions for anything your computer plays, or your microphone. '
                     'Speech is recognised on this computer; audio never leaves it.')
        about.add_legal_section('Speech model', '© NVIDIA (stt_en_fastconformer_hybrid_large_streaming_multi); '
                                'fine-tuned by NowCaption',
                                Gtk.License.CUSTOM, 'Creative Commons Attribution 4.0 International (CC-BY-4.0). '
                                'Fine-tuned by NowCaption on CC-BY and CC0 English speech with text from NVIDIA '
                                'Parakeet TDT 0.6B v2 (CC-BY-4.0), then exported to ONNX and quantized to int8. '
                                'https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi')
        about.add_legal_section('Punctuation model', 'Edge-Punct-Casing by frankyoujian', Gtk.License.APACHE_2_0, None)
        about.add_legal_section('sherpa-onnx', '© k2-fsa', Gtk.License.APACHE_2_0, None)
        about.present(None)  # its own window, not squeezed into the caption box

    # --- desktop portals --------------------------------------------------------------------

    def portals(self) -> None:
        """Shortcut keys and starting at login, through the desktop's portals."""
        p = portal_for(self)
        if not p:
            return
        self.shortcuts = GlobalShortcuts(p, self.on_shortcut, self.on_shortcuts_changed)
        self.autostart = Autostart(p, APP_ID)
        if self.settings.get('shortcuts'):
            self.shortcuts.start(bind=True)

    def on_shortcut(self, shortcut: str) -> None:
        """A global shortcut was pressed (its id in GlobalShortcuts.SHORTCUTS)."""
        if shortcut == 'show-hide':
            self.toggle_captions()
        elif shortcut == 'pause':
            self.listening.set_paused(not self.listening.paused)

    def on_shortcuts_changed(self) -> None:
        """The keys, or whether they work, changed: More options shows them again."""
        if self.options:
            self.options.show_shortcuts()

    def set_shortcuts(self, on: bool) -> None:
        """Turn the keys on (the desktop may ask to confirm them) or off."""
        self.settings.set('shortcuts', on)
        if self.shortcuts:
            self.shortcuts.set_up() if on else self.shortcuts.stop()
        self.on_shortcuts_changed()

    def starts_at_login(self) -> bool:
        """Whether NowCaption starts at login: outside the Flatpak, whether its autostart file
        exists; in it, what the portal last answered."""
        if self.autostart and not in_flatpak():
            return self.autostart.path().exists()
        return self.settings.get('autostart')

    def set_autostart(self, on: bool, done: Callable[[bool, str], None]) -> None:
        """Start at login, or not; done(on, message) says what now happens."""
        if not self.autostart:
            return done(self.starts_at_login(), 'Not available: NowCaption is not on a session bus.')

        def answered(now, message):
            self.settings.set('autostart', now)
            done(now, message)
        self.autostart.set(on, answered)

    # --- what the windows ask of the app ----------------------------------------------------
    # Short forwarders to the collaborators in control/, used by the windows (ui), main() and
    # PyGObject's Ctrl+C (threads_finished, quit), so none of them needs to know how the work is
    # split up. The windows also use a few of the app's own methods and attributes; CONTRIBUTING.md
    # lists all of it. Only set_words, which keeps the app's word list, has work of its own; keep
    # the rest this short.

    @property
    def worker(self) -> engine.RecognitionWorker | None:
        """The recognition worker, once started (the transcript window asks it for snapshots)."""
        return self.listening.worker

    def build_menu(self, several_screens: bool = False, layer: bool = False) -> Gio.Menu:
        """The caption box's menu (see actions.build_menu)."""
        return actions.build_menu(self.history_days(), several_screens, layer)

    def word_effect(self) -> tuple[str, float]:
        """How new words arrive, and the factor of its speed (see Appearance.word_effect)."""
        return self.appearance.word_effect()

    def history_days(self) -> int:
        """How many days sessions are kept, 0 for until deleted."""
        return self.history.history_days()

    def prune_history(self) -> None:
        """Delete the sessions no longer kept."""
        self.history.prune_history()

    def earlier_sessions(self) -> list[tuple[float, str]]:
        """Kept sessions before this one, oldest first, as (start time, text)."""
        return self.history.earlier_sessions()

    def delete_history(self) -> None:
        """Delete every kept session, this one included, and clear the captions."""
        self.history.delete_history()

    def copy_transcript(self) -> None:
        """Copy every kept session and this one (see Saving.copy_transcript)."""
        self.saving.copy_transcript()

    def save_transcript(self) -> None:
        """Ask where to save every kept session and this one, and save them there."""
        self.saving.save_transcript()

    def quit(self) -> bool:
        """Every quit fades the caption box out first (see Quitting.quit), including PyGObject's
        Ctrl+C fallback, which calls app.quit()."""
        return self.quitting.quit()

    def threads_finished(self, timeout: float) -> bool:
        """After the main loop: whether the worker and the capture left native code in time (see
        Listening.threads_finished)."""
        return self.listening.threads_finished(timeout)

    def set_words(self, fixes: Iterable[tuple[str, str]]) -> str:
        """Use and save a new word list ([(heard, write)]). Returns an error message, or ''."""
        self.word_list = [(heard, write) for heard, write in fixes if write.strip()]
        if self.listening.worker:
            self.listening.worker.set_words(words.WordFixes(self.word_list))
        try:
            words.save_words(self.word_list)
        except OSError as e:
            LOG.warning('Could not save the word list: %s', e)
            return f'Could not save the word list: {e.strerror or e}'
        return ''


def main() -> int:
    """Run NowCaption; returns the exit status."""
    # problems are logged with details to stderr (the terminal, or the journal for the menu entry)
    logging.basicConfig(level=os.environ.get('NOWCAPTION_LOG', 'WARNING').upper(),
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    gtk, adw = (Gtk.get_major_version(), Gtk.get_minor_version()), (Adw.get_major_version(), Adw.get_minor_version())
    if gtk < (4, 12) or adw < (1, 5):
        print(f'NowCaption needs GTK 4.12 or newer and libadwaita 1.5 or newer; this system has '
              f'GTK {gtk[0]}.{gtk[1]} and libadwaita {adw[0]}.{adw[1]}. The Flatpak includes both.',
              file=sys.stderr)
        return 1
    app = NowCaption()
    quit_on_signals(app)
    status = app.run(sys.argv)
    if not app.threads_finished(3):  # still loading a model, for example: exit without finalizing
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(status)
    return status
