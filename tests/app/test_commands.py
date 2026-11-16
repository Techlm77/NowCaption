#!/usr/bin/env python3
"""The launch options and the desktop file's actions (Open transcript, More options, Quit)
reaching the NowCaption already running, and the application's own actions on its window:
pausing, hiding, and what More options applies (history days, font, word list).

On the test display like the window tests (support/windows.py); no models or audio pipeline are
started, and the running-instance test uses a private session bus, never the desktop's.
LaunchTests and RunningInstanceTests come from 1.1.16, AppTests from issue #5.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine, words
from support.windows import SKIP, nc, shared_app

if nc:  # GTK and a display are there
    from nowcaption import settings
    from nowcaption.control import commands
    from nowcaption.desktop import portal
    from nowcaption.ui import caption_window, options_window

ROOT = Path(__file__).resolve().parents[2]  # the repository


@unittest.skipIf(nc is None, SKIP)
class LaunchTests(unittest.TestCase):
    """`nowcaption --transcript` and so on, as the desktop file's actions run it."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def launch(self, *names, running=False):
        """What the app does for a launch with these options; running: the captions are up."""
        options = nc.GLib.VariantDict.new(None)
        for name in names:
            options.insert_value(name, nc.GLib.Variant('b', True))
        done = []
        self.app.window = NS() if running else None
        self.addCleanup(setattr, self.app, 'window', None)
        activate = patch.object(self.app, 'activate', lambda: done.append('activate'))
        action = patch.object(self.app, 'activate_action', lambda name, value: done.append(name))
        with activate, action:
            self.assertEqual(commands.on_command_line(self.app, NS(get_options_dict=lambda: options)), 0)
        return done

    def test_a_plain_launch_starts_or_shows_the_captions_as_before(self):
        self.assertEqual(self.launch(), ['activate'])
        self.assertEqual(self.launch(running=True), ['activate'])

    def test_an_action_opens_its_window_in_the_running_nowcaption(self):
        self.assertEqual(self.launch('transcript', running=True), ['transcript'])
        self.assertEqual(self.launch('options', running=True), ['options'])

    def test_an_action_starts_the_captions_first_when_nowcaption_is_not_running(self):
        self.assertEqual(self.launch('transcript'), ['activate', 'transcript'])
        self.assertEqual(self.launch('options'), ['activate', 'options'])

    def test_quit_quits_a_running_nowcaption_and_never_starts_one(self):
        self.assertEqual(self.launch('quit', running=True), ['quit'])
        self.assertEqual(self.launch('quit'), [])

    def test_the_desktop_file_offers_the_actions(self):
        desktop = nc.GLib.KeyFile()
        desktop.load_from_file(str(ROOT / 'data' / f'{settings.APP_ID}.desktop'), nc.GLib.KeyFileFlags.NONE)
        actions = desktop.get_string_list('Desktop Entry', 'Actions')
        self.assertEqual(actions, [name for name, _ in commands.LAUNCH_OPTIONS])
        names = {}
        for action in actions:
            group = f'Desktop Action {action}'
            # Flatpak exports this as `flatpak run ... --command=nowcaption <app id> --transcript`, and
            # AppImage integration replaces only the program, so the option always arrives
            self.assertEqual(desktop.get_string(group, 'Exec'), f'nowcaption --{action}')
            self.assertTrue(self.app.has_action(action))
            names[action] = desktop.get_string(group, 'Name')
        self.assertEqual(names, {'transcript': 'Open transcript', 'options': 'More options',
                                 'toggle': 'Show or hide captions', 'pause': 'Pause or resume captions',
                                 'quit': 'Quit'})


# The NowCaption already running, without a window, models or audio: it prints what it is asked
# to do, then launches NowCaption again as the desktop file's actions do.
RUNNING = """
import subprocess, sys, threading
from nowcaption import app as nc
from nowcaption.control import quitting

AGAIN = 'import sys; from nowcaption import app as nc; sys.exit(nc.NowCaption().run(sys.argv))'
codes = []

def launches():
    for args in (['--transcript'], ['--options'], [], ['--quit']):
        try:
            codes.append(subprocess.run([sys.executable, '-c', AGAIN, *args], timeout=20).returncode)
        except subprocess.TimeoutExpired:
            codes.append('timeout')
    nc.GLib.idle_add(nc.Adw.Application.quit, app)  # in case --quit did not

def started(self, app):
    if not self.window:
        self.window = True
        self.hold()
        threading.Thread(target=launches, daemon=True).start()
    print('activate', flush=True)

def quit(self):
    print('quit', flush=True)
    nc.Adw.Application.quit(self.app)

nc.NowCaption.on_activate = started
nc.NowCaption.open_transcript = lambda self: print('transcript', flush=True)
nc.NowCaption.open_options = lambda self: print('options', flush=True)
quitting.Quitting.request_quit = quit
app = nc.NowCaption()
app.fullscreen_watch.bus = None
app.panel_float.bus = None  # nor change the panels of the desktop running the tests
status = app.run(['nowcaption'])
for thread in threading.enumerate():
    if thread is not threading.current_thread():
        thread.join(20)
print(*codes, flush=True)
sys.exit(status)
"""


# A session bus that starts no services, so nothing from the desktop (portals and so on) runs on it
BUS = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


@unittest.skipIf(nc is None, SKIP)
@unittest.skipUnless(shutil.which('dbus-run-session'), 'dbus-run-session is not installed')
class RunningInstanceTests(unittest.TestCase):
    def test_a_later_launch_hands_its_action_to_the_running_nowcaption(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder, 'session.conf')
            config.write_text(BUS)
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), NOWCAPTION_REEXEC='1')
            run = subprocess.Popen(['dbus-run-session', f'--config-file={config}', '--', sys.executable, '-c', RUNNING],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
            try:
                out, err = run.communicate(timeout=120)
            except subprocess.TimeoutExpired:  # the bus and every NowCaption on it go too
                os.killpg(run.pid, signal.SIGKILL)
                out, err = run.communicate()
        self.assertEqual(out.split(), ['activate', 'transcript', 'options', 'activate', 'quit', '0', '0', '0', '0'],
                         err)
        self.assertEqual(run.returncode, 0, err)


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from support.windows import shared_app
        cls.app = shared_app()

    def setUp(self):
        self.window = caption_window.CaptionWindow(self.app)
        self.app.window = self.window
        self.calls = []
        self.app.listening.capture = NS(start=lambda device, gen: self.calls.append(('start', gen)),
                              stop=lambda: self.calls.append(('stop',)))
        self.app.listening.worker = NS(set_source=lambda gen: self.calls.append(('source', gen)),
                             set_words=lambda fixes: self.calls.append(('words', fixes.fixes)))
        self.app.listening.ready = True

    def tearDown(self):
        self.app.listening.set_paused(False)
        self.app.listening.worker = None
        self.app.listening.ready = False
        if self.app.hidden:
            self.app.toggle_captions()
        self.window.disconnect_monitors()
        self.window.destroy()
        self.app.window = None
        self.app.settings.set('history_days', engine.HISTORY_DAYS)
        self.app.lookup_action('history-days').change_state(nc.GLib.Variant('s', str(engine.HISTORY_DAYS)))
        self.app.settings.set('font', '')
        self.app.lookup_action('font').change_state(nc.GLib.Variant('s', ''))
        if self.app.options:
            self.app.options.close()

    def test_pause_stops_capture_and_resume_listens_again(self):
        gen = self.app.listening.source_gen
        self.app.activate_action('pause', None)
        self.assertTrue(self.app.listening.paused)
        self.assertEqual(self.calls, [('source', gen + 1), ('stop',)])
        self.assertIn('paused', self.window.status)
        self.app.listening.start_capture()  # a source change or Clear while paused does not listen
        self.assertEqual(len(self.calls), 2)
        self.app.activate_action('pause', None)
        self.assertFalse(self.app.listening.paused)
        self.assertEqual(self.calls[2:], [('source', gen + 2), ('start', gen + 2)])
        self.assertNotIn('paused', self.window.status)

    def test_hiding_keeps_the_window_and_starting_again_shows_it(self):
        self.window.present()
        self.app.activate_action('toggle', None)
        self.assertTrue(self.app.hidden)
        self.assertFalse(self.window.get_visible())
        self.app.on_activate(self.app)  # NowCaption started again
        self.assertFalse(self.app.hidden)
        self.assertTrue(self.window.get_visible())

    def test_toggle_and_pause_only_act_on_a_running_nowcaption(self):
        done = []
        options = nc.GLib.VariantDict.new(None)
        options.insert_value('toggle', nc.GLib.Variant('b', True))
        line = NS(get_options_dict=lambda: options)
        self.app.window = None
        with patch.object(self.app, 'activate', lambda: done.append('activate')), \
                patch.object(self.app, 'activate_action', lambda name, value: done.append(name)):
            commands.on_command_line(self.app, line)
        self.assertEqual(done, ['activate'])  # starting it shows the captions; it does not hide them

    def test_history_days_apply_with_apply_and_change_the_menu(self):
        self.app.open_options()
        options = self.app.options
        options.chosen.lookup_action('history-days').activate(nc.GLib.Variant('s', '30'))
        self.assertEqual(self.app.history_days(), 7)  # not before Apply
        options.apply()
        self.assertEqual(self.app.history_days(), 30)
        menu = self.app.build_menu()
        text = []
        for i in range(menu.get_n_items()):
            section = menu.get_item_link(i, 'section')
            for j in range(section.get_n_items()):
                label = section.get_item_attribute_value(j, 'label', None)
                if label:
                    text.append(label.get_string())
        self.assertIn('Keep 30 days of history', text)
        self.assertIn('Pause captions', text)

    def test_the_font_shows_in_the_sample_first_then_the_captions(self):
        self.app.open_options()
        options = self.app.options
        options.chosen.lookup_action('font').activate(nc.GLib.Variant('s', 'DejaVu Serif'))
        self.assertIn('"DejaVu Serif"', options.sample_css.to_string())
        self.assertEqual(self.app.settings.get('font'), '')
        options.apply()
        self.assertEqual(self.app.settings.get('font'), 'DejaVu Serif')
        self.assertEqual(self.window.look[1], 'DejaVu Serif')
        self.assertIn('"DejaVu Serif"', self.window.css.to_string())

    def test_more_options_names_the_command_this_install_has(self):
        OW = options_window.OptionsWindow
        with patch.object(portal, 'in_flatpak', lambda: True):
            self.assertEqual(OW.launch_command(), 'flatpak run io.github.Techlm77.NowCaption')
        with patch.object(portal, 'in_flatpak', lambda: False), patch.dict(os.environ, APPIMAGE='/opt/NowCaption.AppImage'):
            self.assertEqual(OW.launch_command(), '/opt/NowCaption.AppImage')
        with patch.object(portal, 'in_flatpak', lambda: False), patch.dict(os.environ, clear=False) as env:
            env.pop('APPIMAGE', None)
            self.assertEqual(OW.launch_command(), 'nowcaption')

    def test_word_fixes_added_in_more_options_reach_the_worker_and_the_file(self):
        self.app.open_options()
        options = self.app.options
        options.heard_entry.set_text('in  video')
        options.write_entry.set_text('Nvidia')
        options.add_fix()
        self.assertEqual(self.app.word_list, [('in video', 'Nvidia')])
        self.assertEqual(words.load_words(), [('in video', 'Nvidia')])
        self.assertEqual(self.calls[-1], ('words', [('in video', 'Nvidia')]))
        self.assertEqual(len(options.fix_rows), 1)
        options.remove_fix(('in video', 'Nvidia'))
        self.assertEqual(words.load_words(), [])
        self.assertEqual(len(options.fix_rows), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
