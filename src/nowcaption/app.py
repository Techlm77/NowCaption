"""NowCaption: on-device live captions for anything your computer plays, or your microphone."""
import logging
import os
import signal
import sys
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import __version__, audio, engine, portal, words  # noqa: E402
from .caption_window import CaptionWindow  # noqa: E402
from .options_window import OptionsWindow  # noqa: E402
from .plasma import FULLSCREEN_PATH, FULLSCREEN_XML, FullscreenWatch, PanelFloat  # noqa: E402
from .settings import APP_ID, Settings  # noqa: E402
from .style import COLOURS, FADE_OUT_MS, POSITIONS, SIZES, STEPPERS, WORD_EFFECTS, WORD_SPEEDS  # noqa: E402
from .transcript_window import TranscriptWindow, session_heading  # noqa: E402

LOG = logging.getLogger('nowcaption')


class NowCaption(Adw.Application):
    """Owns the window, the capture and the recognition worker. All methods run on the GTK
    thread; worker and capture callbacks are passed here with GLib.idle_add."""

    # the desktop file's actions run `nowcaption --transcript` and so on: see on_command_line
    LAUNCH_OPTIONS = (('transcript', 'Open the transcript'), ('options', 'Open More options'),
                      ('toggle', 'Show or hide the captions'), ('pause', 'Pause or resume the captions'),
                      ('quit', 'Quit NowCaption if it is running'))
    RUNNING_ONLY = ('toggle', 'pause')  # for a NowCaption already running; starting one just starts it

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        for name, description in self.LAUNCH_OPTIONS:
            self.add_main_option(name, 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, description, None)
        self.settings = Settings()
        self.window = self.worker = None
        self.ready = self.closing = False
        self.source_gen = self.ui_gen = 0
        self.capture_failures = self.recognition_failures = 0
        self.retry_id = None
        self.recognition_retry_id = None
        self.recognition_retry_token = 0
        self.pending_saves = 0
        self.started = time.time()
        self.history_path = engine.history_file(self.started)  # created once something is said
        self.transcript = None  # the transcript window, while it is open
        self.options = None  # the More options window, while it is open
        self.save_queues = {}  # destination URI -> accepted writes, in snapshot order
        self.quit_requested = False
        self.fading_out = False
        self.fullscreen_watch = FullscreenWatch(APP_ID)
        self.panel_float = PanelFloat(self.settings)
        self.paused = False  # Pause captions: nothing is captured until it is turned off
        self.hidden = False  # the captions were hidden (toggle_captions); they keep captioning
        self.word_list = words.load_words()  # [(heard, write)], edited under More options
        self.shortcuts = self.autostart = None  # see portals(), once on the session bus
        self.capture = audio.Capture(
            on_audio=lambda gen, pcm: self.worker.feed(gen, pcm),
            on_started=lambda gen: GLib.idle_add(self.capture_started, gen),
            on_ended=lambda gen, reason: GLib.idle_add(self.capture_ended, gen, reason))
        self.connect('command-line', self.on_command_line)
        self.connect('activate', self.on_activate)
        self.connect('shutdown', self.on_shutdown)
        self.add_actions()

    def build_menu(self, several_screens=False, layer=False):
        """The caption box's menu, in sections: how captions look, the box's place, the
        transcript, then More options (a window for the rest) and the app. CaptionWindow.update_menu
        fills the 'custom' items with rows of buttons that keep the menu open."""
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
        span = engine.history_span(self.history_days())
        menu.append_item(section('Transcript', item('Open transcript', 'app.transcript'),
                                 item(f'Keep {span} of history' if span else 'Keep history', 'app.history'),
                                 item('Clear this session', 'app.clear')))
        menu.append_item(section(None, item('Pause captions', 'app.pause'), item('More options…', 'app.options'),
                                 item('About NowCaption', 'app.about'), item('Quit', 'app.quit')))
        return menu

    def add_actions(self):
        def act(name, fn, accels=()):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', lambda *_: fn())
            self.add_action(a)
            if accels:
                self.accels['app.' + name] = list(accels)
        self.accels = {}  # registered once we know the window can take keyboard focus
        source = Gio.SimpleAction.new_stateful('source', GLib.VariantType.new('s'),
                                               GLib.Variant('s', self.settings.get('source')))
        source.connect('activate', self.on_source)
        self.add_action(source)
        colour = Gio.SimpleAction.new_stateful('colour', GLib.VariantType.new('s'),
                                               GLib.Variant('s', self.settings.get('colour')))
        colour.connect('activate', self.on_colour)
        self.add_action(colour)
        act('larger', lambda: self.resize(+1), ['<Control>plus', '<Control>equal'])
        act('smaller', lambda: self.resize(-1), ['<Control>minus'])
        act('lighter', lambda: self.opacity(-0.15))
        act('darker', lambda: self.opacity(+0.15))
        position = Gio.SimpleAction.new_stateful('position', GLib.VariantType.new('s'),
                                                 GLib.Variant('s', self.settings.get('position')))
        position.connect('activate', self.on_position)
        self.add_action(position)
        for name, key, handler in (('word-effect', 'word_effect', self.on_word_effect),
                                   ('word-speed', 'word_speed', self.on_word_speed),
                                   ('history-days', 'history_days', self.on_history_days),
                                   ('font', 'font', self.on_font)):
            action = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new('s'),
                                                   GLib.Variant('s', str(self.settings.get(key))))
            action.connect('activate', handler)
            self.add_action(action)
        history = Gio.SimpleAction.new_stateful('history', None, GLib.Variant('b', self.settings.get('history')))
        history.connect('activate', self.on_history)
        self.add_action(history)
        pause = Gio.SimpleAction.new_stateful('pause', None, GLib.Variant('b', False))
        pause.connect('activate', lambda action, value: self.set_paused(not self.paused))
        self.add_action(pause)
        act('toggle', self.toggle_captions)
        act('transcript', self.open_transcript, ['<Control>t'])
        act('options', self.open_options, ['<Control>comma'])
        act('restart-listening', self.restart_listening)
        act('reset', lambda: (self.window.reset_position(), self.window.reset_size()))
        act('reset-position', lambda: self.window.reset_position())
        act('reset-size', lambda: self.window.reset_size())
        act('next-screen', lambda: self.window.next_monitor())
        act('copy', self.copy_transcript)
        act('save', self.save_transcript, ['<Control>s'])
        act('clear', self.clear_transcript)
        act('about', self.about)
        act('quit', self.request_quit, ['<Control>q'])

    def on_source(self, action, value):
        if self.closing or self.quit_requested:
            return
        self.cancel_recognition_retry(reset=True)
        action.set_state(value)
        self.settings.set('source', value.get_string())
        if self.ready:
            self.start_capture()
        elif self.worker and not self.closing:
            # replaces the load error until the model loads or fails again
            self.window.show_failure('model', 'Retrying the speech model…')
            self.worker.restart()

    def restart_listening(self):
        """Choose the same sound source again, which starts capture again, or loads the speech
        model again if it failed: More options' Restart, after a problem or a change of devices."""
        source = self.lookup_action('source')
        source.activate(source.get_state())

    def on_position(self, action, value):
        if value.get_string() not in POSITIONS:
            return
        action.set_state(value)
        self.settings.set('position', value.get_string())
        if self.window:
            self.window.reposition()

    def on_word_effect(self, action, value):
        if value.get_string() in WORD_EFFECTS:
            action.set_state(value)
            self.settings.set('word_effect', value.get_string())
            self.show_word_effect()

    def on_word_speed(self, action, value):
        if value.get_string() in WORD_SPEEDS:
            action.set_state(value)
            self.settings.set('word_speed', value.get_string())
            self.show_word_effect()

    def word_effect(self):
        """How new words arrive, and the factor of its speed, as CaptionText.set_effect takes them."""
        return self.settings.get('word_effect'), WORD_SPEEDS[self.settings.get('word_speed')][1]

    def show_word_effect(self):
        if self.window:
            self.window.caption.set_effect(*self.word_effect())

    def open_options(self):
        if self.options is None:
            self.options = OptionsWindow(self)
            self.options.connect('close-request', self.on_options_closed)
        self.options.present()

    def on_options_closed(self, window):
        self.options = None
        return False

    def on_history_days(self, action, value):
        """Keep sessions this many days (0: until deleted). A shorter time deletes the older ones now."""
        try:
            days = int(value.get_string())
        except ValueError:
            return
        if days not in engine.HISTORY_CHOICES:
            return
        action.set_state(value)
        self.settings.set('history_days', days)
        self.prune_history()
        if self.window:
            self.window.refresh_menu()
        if self.transcript:
            self.transcript.show_title()
            self.transcript.reload()

    def history_days(self):
        """How many days sessions are kept, 0 for until deleted."""
        return self.settings.get('history_days')

    def prune_history(self):
        engine.prune_history(days=self.history_days())

    def on_font(self, action, value):
        action.set_state(value)
        self.settings.set('font', ' '.join(value.get_string().split()))
        if self.window:
            self.window.restyle()

    def set_words(self, fixes):
        """Use and save a new word list ([(heard, write)]). Returns an error message, or ''."""
        self.word_list = [(heard, write) for heard, write in fixes if write.strip()]
        if self.worker:
            self.worker.set_words(words.WordFixes(self.word_list))
        try:
            words.save_words(self.word_list)
        except OSError as e:
            LOG.warning('Could not save the word list: %s', e)
            return f'Could not save the word list: {e.strerror or e}'
        return ''

    # --- pause and show/hide ----------------------------------------------------------------

    def set_paused(self, paused):
        """Pause captions: capture stops, so nothing is heard or kept, and the words already
        heard become a finished line. Resuming listens again from the chosen source."""
        if paused == self.paused or self.closing or self.quit_requested:
            return
        self.paused = paused
        self.lookup_action('pause').set_state(GLib.Variant('b', paused))
        if paused:
            self.cancel_recognition_retry(reset=True)
            if self.retry_id:
                GLib.source_remove(self.retry_id)
                self.retry_id = None
            self.source_gen += 1  # audio still on its way from the old capture is dropped
            if self.worker:
                self.worker.set_source(self.source_gen)  # the words being spoken become a line
            self.capture.stop()
            if self.window:
                self.window.clear_failure('capture')
                self.window.show_failure('paused', 'Captions paused. Nothing is being listened to.')
        else:
            if self.window:
                self.window.clear_failure('paused')
            self.start_capture()

    def toggle_captions(self):
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

    # --- desktop portals --------------------------------------------------------------------

    def portals(self):
        """Shortcut keys and starting at login, through the desktop's portals."""
        p = portal.portal_for(self)
        if not p:
            return
        self.shortcuts = portal.GlobalShortcuts(p, self.on_shortcut, self.on_shortcuts_changed)
        self.autostart = portal.Autostart(p, APP_ID)
        if self.settings.get('shortcuts'):
            self.shortcuts.start(bind=True)

    def on_shortcut(self, shortcut):
        if shortcut == 'show-hide':
            self.toggle_captions()
        elif shortcut == 'pause':
            self.set_paused(not self.paused)

    def on_shortcuts_changed(self):
        if self.options:
            self.options.show_shortcuts()

    def set_shortcuts(self, on):
        """Turn the keys on (the desktop may ask to confirm them) or off."""
        self.settings.set('shortcuts', on)
        if self.shortcuts:
            self.shortcuts.set_up() if on else self.shortcuts.stop()
        self.on_shortcuts_changed()

    def starts_at_login(self):
        if self.autostart and not portal.in_flatpak():
            return self.autostart.path().exists()
        return self.settings.get('autostart')

    def set_autostart(self, on, done):
        """Start at login, or not; done(on, message) says what now happens."""
        if not self.autostart:
            return done(self.starts_at_login(), 'Not available: NowCaption is not on a session bus.')

        def answered(now, message):
            self.settings.set('autostart', now)
            done(now, message)
        self.autostart.set(on, answered)

    def on_history(self, action, value):
        keep = not action.get_state().get_boolean()
        action.set_state(GLib.Variant('b', keep))
        self.settings.set('history', keep)
        if self.worker:
            self.worker.set_history(self.history_path if keep else None)
        if self.transcript:
            self.transcript.show_title()

    def open_transcript(self):
        if self.transcript is None:
            self.transcript = TranscriptWindow(self)
            self.transcript.connect('close-request', self.on_transcript_closed)
        self.transcript.present()

    def on_transcript_closed(self, window):
        self.transcript = None  # the window lets go of the rest itself (TranscriptWindow.on_close)
        return False

    def earlier_sessions(self):
        """Kept sessions before this one, oldest first, as (start time, text)."""
        sessions = []
        cutoff = engine.history_cutoff(self.history_days())
        for started, path in engine.history_files():
            if path == self.history_path:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    continue  # no longer kept, even if prune_history has not deleted it yet
                text = path.read_text(errors='replace').strip()
            except OSError as e:
                LOG.warning('Could not read the transcript %s: %s', path, e)
                continue
            if text:
                sessions.append((started, text))
        return sessions

    def full_transcript(self, current):
        """Every kept session and this one, each under its heading, as Copy and Save give it."""
        parts = [f'{session_heading(started)}\n{text}' for started, text in self.earlier_sessions()]
        if current.strip():
            parts.append(f'{session_heading(self.started)}\n{current.strip()}')
        return '\n\n'.join(parts)

    def delete_history(self):
        """Delete every kept session, this one included, and clear the captions."""
        for _, path in engine.history_files():
            try:
                path.unlink()
            except OSError as e:
                LOG.warning('Could not delete the transcript %s: %s', path, e)
        self.clear_transcript()
        if self.worker and self.settings.get('history'):
            self.worker.set_history(self.history_path)  # what is said next starts a new file
        if self.transcript:
            self.transcript.reload()

    def on_colour(self, action, value):
        if value.get_string() not in COLOURS:
            return
        action.set_state(value)
        self.settings.set('colour', value.get_string())
        if self.window:
            self.window.restyle()

    def resize(self, step):
        size = self.settings.get('size')
        i = min(range(len(SIZES)), key=lambda k: abs(SIZES[k] - size))
        self.settings.set('size', SIZES[max(0, min(len(SIZES) - 1, i + step))])
        self.window.restyle()

    def opacity(self, step):
        self.settings.set('opacity', round(max(0.2, min(1.0, self.settings.get('opacity') + step)), 2))
        self.window.restyle()

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
        invocation.return_value(None)
        if method == 'Changed':
            self.on_fullscreen(params.unpack()[0])

    def on_fullscreen(self, fullscreen):
        if self.window:
            self.window.set_covered(bool(fullscreen))

    def on_command_line(self, app, command_line):
        """Each launch, this one or a later one that GLib hands to this running NowCaption:
        start the captions, or show them, then open what a launcher action asked for. --quit
        quits a running NowCaption and never starts one."""
        options = command_line.get_options_dict()
        chosen = [name for name, _ in self.LAUNCH_OPTIONS if options.contains(name)]
        if 'quit' in chosen:
            if self.window:
                self.activate_action('quit', None)
            return 0
        if not self.window:  # starting NowCaption shows its captions and listens
            chosen = [name for name in chosen if name not in self.RUNNING_ONLY]
        if not (self.window and chosen):  # an open window is not raised first, so the one asked for gets focus
            self.activate()
        elif self.hidden and 'toggle' not in chosen:
            self.toggle_captions()  # a window asked for from hidden captions brings them back too
        for name in chosen:
            self.activate_action(name, None)
        return 0

    def on_activate(self, app):
        if self.window:
            if self.hidden:  # starting NowCaption again shows hidden captions
                return self.toggle_captions()
            return self.window.present()
        self.window = CaptionWindow(self)
        self.window.connect('close-request', self.on_window_close)
        if not self.window.layer:  # an overlay never has keyboard focus, so shortcuts would never fire
            for action, keys in self.accels.items():
                self.set_accels_for_action(action, keys)
        self.window.present()
        self.window.show_status('Loading speech model…')
        # models load on the worker thread, so the window stays responsive meanwhile
        self.worker = engine.RecognitionWorker(lambda kind, value: GLib.idle_add(self.on_worker, kind, value))
        self.prune_history()
        if self.settings.get('history'):
            self.worker.set_history(self.history_path)
        if self.word_list:
            self.worker.set_words(words.WordFixes(self.word_list))
        self.worker.start()
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
        self.closing = True
        self.fullscreen_watch.stop(sync=True)
        self.panel_float.stop()
        if self.shortcuts:
            self.shortcuts.stop()
        self.cancel_recognition_retry()
        if self.retry_id:
            GLib.source_remove(self.retry_id)
            self.retry_id = None
        self.capture.stop()
        if self.worker:
            self.worker.stop()

    def threads_finished(self, timeout):
        """After the main loop, wait for the recognition worker and the capture to leave native
        code (never from the window's shutdown: a stalled model must not freeze it). Python 3.12
        and 3.13 end a daemon thread that is still in native code when the interpreter exits
        with pthread_exit, and sherpa-onnx's C++ code then aborts the process."""
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout)
        return self.capture.wait_stopped(timeout) and not (self.worker and self.worker.is_alive())

    def request_quit(self):
        """Keep accepted saves alive until their snapshot and async write finish."""
        if self.pending_saves:
            self.quit_requested = True
            if self.window:
                self.window.show_status('Finishing transcript save before quitting…', sticky=True)
            return
        self.quit()

    def quit(self):
        """Fade the caption box out, then quit. A second request, or a fade that stalls
        because the screen stopped drawing, quits at once."""
        if self.window and not self.fading_out:
            self.fading_out = True
            GLib.timeout_add(FADE_OUT_MS + 350, self.quit)
            self.window.fade(self.window.box, None, 0.0, FADE_OUT_MS, self.quit)
            return False
        Adw.Application.quit(self)
        return False

    def on_window_close(self, window):
        self.request_quit()
        return True

    def cancel_recognition_retry(self, reset=False):
        self.recognition_retry_token += 1
        if self.recognition_retry_id:
            GLib.source_remove(self.recognition_retry_id)
            self.recognition_retry_id = None
        if reset:
            self.recognition_failures = 0

    def retry_recognition(self, token, source_gen):
        if token != self.recognition_retry_token:
            return False
        self.recognition_retry_id = None
        self.recognition_retry_token += 1
        if self.worker and not self.closing and source_gen == self.source_gen:
            self.worker.restart(recover_only=True, source_gen=source_gen)
        return False

    # --- recognition worker events ----------------------------------------------------------

    def on_worker(self, kind, value):
        if self.closing:
            return False
        if kind == 'ready':
            self.ready = True
            self.window.clear_failure('model')
            self.start_capture()
        elif kind == 'caption':
            gen, text = value
            if gen == self.ui_gen:  # ignore captions from before the last Clear
                self.window.show_text(text)
                if self.transcript:
                    self.transcript.changed()
        elif kind == 'recognition-ok':
            if value == self.source_gen:
                self.cancel_recognition_retry(reset=True)
                self.window.clear_failure('recognition')
        elif kind == 'snapshot':
            fn, text = value
            fn(text)
        elif kind == 'snapshot-error':
            fn, message = value
            if fn:
                fn(message)
            else:
                self.window.show_status(f'Could not prepare the transcript: {message}', seconds=8)
        elif kind == 'warning':
            self.window.show_status(value, seconds=6)
        elif kind == 'overload':
            self.window.show_status(f'Falling behind: skipped {value:.1f} s of audio', seconds=4)
        elif kind == 'load-error':
            self.ready = False
            self.window.show_failure('model', f'{value} To try again, open More options and press Restart.')
        elif kind == 'error':
            source_gen, stream_gen, message = value
            if (source_gen != self.source_gen or
                    stream_gen != getattr(self.worker, 'stream_gen', stream_gen) or
                    not getattr(self.worker, 'broken', True)):
                return False  # an obsolete source/stream cannot spend the current retry budget
            self.cancel_recognition_retry()
            self.recognition_failures += 1
            if self.recognition_failures <= 3:
                delay = 2 ** self.recognition_failures
                self.window.show_failure('recognition', f'{message}. Restarting in {delay} s…')
                self.recognition_retry_id = GLib.timeout_add_seconds(
                    delay, self.retry_recognition, self.recognition_retry_token, self.source_gen)
            else:
                self.window.show_failure('recognition',
                                         f'{message}. To try again, open More options and press Restart.')
        return False

    # --- capture ----------------------------------------------------------------------------

    def start_capture(self):
        if not self.ready or self.closing or self.paused:
            return
        self.cancel_recognition_retry()
        if self.retry_id:
            GLib.source_remove(self.retry_id)
            self.retry_id = None
        mic = self.settings.get('source') == 'mic'
        self.source_gen += 1
        self.worker.set_source(self.source_gen)
        # NOWCAPTION_SOURCE names any PulseAudio/PipeWire source, overriding the menu choice
        device = os.environ.get('NOWCAPTION_SOURCE') or (audio.MICROPHONE if mic else audio.DESKTOP)
        try:
            self.capture.start(device, self.source_gen)
        except Exception as e:
            return self.capture_ended(self.source_gen, str(e))
        if not self.window.text:
            self.window.show_status('Listening to the microphone…' if mic else 'Listening to desktop audio…')

    def capture_started(self, gen):
        if gen == self.source_gen and self.capture_failures:
            self.capture_failures = 0
            self.window.clear_failure('capture')
            self.window.show_status('Audio is back.', seconds=3)
        return False

    def capture_ended(self, gen, reason):
        """Capture stopped on its own (device gone, sound server restarted…): retry with backoff."""
        if gen != self.source_gen or self.closing:
            return False
        delay = min(30, 2 ** self.capture_failures)
        self.capture_failures += 1
        LOG.warning('Audio capture stopped (%s); retrying in %d s', reason, delay)
        self.window.show_failure('capture', f'Audio capture stopped ({reason}). Retrying in {delay} s…')
        self.retry_id = GLib.timeout_add_seconds(delay, self.retry_capture)
        return False

    def retry_capture(self):
        self.retry_id = None
        self.start_capture()
        return False

    # --- transcript -------------------------------------------------------------------------

    def copy_transcript(self):
        def copy(text):
            text = self.full_transcript(text)
            self.window.get_clipboard().set(text)
            self.window.show_status('Transcript copied.' if text else 'Nothing to copy yet.', seconds=3)
        if self.worker:
            try:
                self.worker.snapshot(copy)
            except Exception as e:
                self.window.show_status(f'Could not copy the transcript: {e}', seconds=8)

    def clear_transcript(self):
        if self.worker and not self.closing and not self.quit_requested:
            self.cancel_recognition_retry(reset=True)
            self.ui_gen += 1  # captions already on their way from before the clear are dropped
            self.worker.clear(self.ui_gen)
            self.window.show_text('')
            # Discard pre-Clear audio buffered in GStreamer as well as in recognition.
            if self.ready:
                self.start_capture()

    def save_transcript(self):
        if not self.worker or self.closing or self.quit_requested:
            return
        dialog = Gtk.FileDialog(title='Save transcript', initial_name=GLib.DateTime.new_now_local().format(
            'Captions %Y-%m-%d %H.%M.txt'))

        def chosen(d, result):
            try:
                f = d.save_finish(result)
            except GLib.Error as e:
                if not e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self.window.show_status(f'Could not save the transcript: {e.message}', seconds=8)
                return
            if self.closing or self.quit_requested:
                return
            self.begin_save()

            def failed_snapshot(error):
                self.window.show_status(f'Could not save the transcript: {error}', seconds=8)
                self.finish_save(False)

            try:
                self.worker.snapshot(lambda text: self.write_transcript(f, self.full_transcript(text), tracked=True),
                                     on_error=failed_snapshot)
            except Exception as e:
                failed_snapshot(e)

        dialog.save(None, None, chosen)  # its own window: the caption overlay is too small to host it

    def begin_save(self):
        self.pending_saves += 1
        self.hold()

    def finish_save(self, success):
        self.pending_saves -= 1
        if not success:
            # A failed save needs a visible error and an opportunity to retry.
            self.quit_requested = False
        should_quit = self.quit_requested and not self.pending_saves
        self.release()
        if should_quit:
            self.quit()

    def write_transcript(self, f, text, tracked=False):
        """Queue saves to each destination so an older write cannot replace newer text."""
        if not tracked:
            self.begin_save()
        key = f.get_uri()
        queue = self.save_queues.setdefault(key, [])
        queue.append((f, text))
        if len(queue) == 1:
            self.start_save(key)

    def start_save(self, key):
        """Write one queued snapshot asynchronously; other destinations can proceed in parallel."""
        queue = self.save_queues[key]
        f, text = queue[0]

        def finished(success):
            queue.pop(0)
            if not queue:
                del self.save_queues[key]
            self.finish_save(success)
            if queue:
                self.start_save(key)

        def failed(error):
            LOG.warning('Saving the transcript failed: %s', error)
            self.window.show_status(f'Could not save the transcript: {error}', seconds=8)
            finished(False)

        def written(f, result):
            try:
                f.replace_contents_finish(result)
            except GLib.Error as e:
                failed(e.message)
                return
            self.window.show_status(f'Transcript saved to {f.get_basename()}.', seconds=4)
            finished(True)
        try:
            f.replace_contents_bytes_async(GLib.Bytes.new((text + '\n').encode()), None, False,
                                           Gio.FileCreateFlags.REPLACE_DESTINATION, None, written)
        except Exception as e:
            failed(e)

    def about(self):
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


def quit_on_signals(app):
    """Quit the way Quit does on SIGTERM and SIGHUP (kill, logging out, shutting down), so the
    newest lines still reach the history. A second signal quits without waiting for a save to
    finish. Returns the GLib sources."""
    try:
        gi.require_version('GLibUnix', '2.0')
        from gi.repository import GLibUnix
    except (ValueError, ImportError):  # GLib before 2.80
        GLibUnix = None
    # GLib 2.80's GLibUnix (Ubuntu 24.04, and so the AppImage) has only signal_add_full
    add = getattr(GLibUnix, 'signal_add', None) or GLib.unix_signal_add

    def on_signal(*_):
        if app.quit_requested or app.fading_out:
            app.quit()
        else:
            app.request_quit()
        return GLib.SOURCE_CONTINUE
    return [add(GLib.PRIORITY_DEFAULT, signum, on_signal) for signum in (signal.SIGTERM, signal.SIGHUP)]


def main():
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
