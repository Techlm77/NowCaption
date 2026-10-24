#!/usr/bin/env python3
"""Reaching the menu without a mouse or keyboard focus, for 1.1.16. A long press on the
captions opens the menu where a right-click would; the desktop file's actions (Open
transcript, More options and Quit) reach the NowCaption already running.

Runs on the test display like test_window.py. No models or audio pipeline are started, and
the running-instance test uses a private session bus, never the desktop's.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import test_window
from test_window import FakeLayerShell, nc, shared_app

ROOT = Path(__file__).resolve().parent.parent
SKIP = getattr(test_window, 'SKIP', '')


def press():
    """A stand-in for the gesture of one press, recording the states it is given."""
    gesture = NS(states=[])
    gesture.set_state = gesture.states.append
    return gesture


@unittest.skipIf(nc is None, SKIP)
class LongPressTests(unittest.TestCase):
    """On layer shell, where the captions never have keyboard focus."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.shell = FakeLayerShell()
        self.shell.modes = []
        self.shell.set_keyboard_mode = lambda window, mode: self.shell.modes.append(mode)
        patcher = patch.object(nc, 'LayerShell', self.shell)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ('position', 'x', 'y', 'width', 'rows', 'size'):
            self.addCleanup(self.app.settings.set, key, nc.DEFAULTS[key])
        self.app.settings.set('size', 30)
        self.window = nc.CaptionWindow(self.app)
        self.app.window = self.window
        self.addCleanup(self.window.destroy)
        self.addCleanup(self.window.disconnect_monitors)
        self.addCleanup(setattr, self.app, 'window', None)
        if not self.window.monitor:
            self.skipTest('No monitor advertised by the display')
        self.window.show_text('so we took the long road back past the old sawmill')
        self.window.present()
        self.settle()
        self.shell.modes = []  # from here on; the window starts without keyboard input

    def settle(self, seconds=0.0):
        context, end = nc.GLib.MainContext.default(), time.monotonic() + seconds
        while True:
            while context.pending():
                context.iteration(False)
            if time.monotonic() >= end:
                return
            time.sleep(0.01)

    def menu(self):
        ok, rect = self.window.popover.get_pointing_to()
        return self.window.popover.get_position(), (rect.x, rect.y, rect.width, rect.height)

    def test_the_long_press_is_for_touch_and_apart_from_the_drag(self):
        controllers = self.window.observe_controllers()
        controllers = [controllers.get_item(i) for i in range(controllers.get_n_items())]
        holds = [c for c in controllers if isinstance(c, nc.Gtk.GestureLongPress)]
        drag = next(c for c in controllers if isinstance(c, nc.Gtk.GestureDrag))
        self.assertEqual(len(holds), 1)
        # a mouse has its right button, and pressing it still before dragging stays a drag
        self.assertTrue(holds[0].get_touch_only())
        # in a group of its own: when it claims the press, GTK denies the press to the drag and
        # ends it; when the drag claims it first (an edge), GTK cancels the long press
        self.assertNotIn(drag, holds[0].get_group())

    def test_holding_a_finger_on_the_captions_opens_the_menu_where_a_right_click_would(self):
        w = self.window
        w.place(100, 50)
        x, y = w.x + 50, w.y + 20
        w.point_menu(x, y)
        right_click = self.menu()
        w.point_menu(w.x + 30, w.y + 30)  # elsewhere, so the long press must point it again
        w.on_drag_begin(press(), x, y)  # the finger going down starts a move
        self.assertTrue(w.dragging)
        hold = press()
        w.on_long_press(hold, x, y)
        self.assertEqual(hold.states, [nc.Gtk.EventSequenceState.CLAIMED])
        self.assertEqual(self.menu(), right_click)
        self.assertEqual(self.shell.modes, [FakeLayerShell.KeyboardMode.ON_DEMAND])  # its items take keys
        self.settle(0.3)
        self.assertTrue(w.popover.get_visible())
        w.popover.popdown()
        self.assertEqual(self.shell.modes[-1], FakeLayerShell.KeyboardMode.NONE)
        w.on_drag_end(None, 0, 0)  # as GTK ends the denied drag
        self.assertEqual((self.app.settings.get('x'), self.app.settings.get('y')), (100, 50))

    def test_a_long_press_on_a_docked_strip_opens_the_menu_away_from_its_edge(self):
        w = self.window
        self.app.activate_action('position', nc.GLib.Variant('s', 'bottom'))
        self.settle()
        x, y = w.get_width() // 2, w.get_height() - 5  # inside, away from the edge that resizes
        w.on_drag_begin(press(), x, y)
        self.assertFalse(w.resizing)
        hold = press()
        w.on_long_press(hold, x, y)
        self.assertEqual(hold.states, [nc.Gtk.EventSequenceState.CLAIMED])
        self.assertEqual(self.menu(), (nc.Gtk.PositionType.TOP, (x, 0, 1, 1)))
        self.settle(0.3)
        w.popover.popdown()

    def test_holding_still_on_an_edge_keeps_resizing_and_opens_no_menu(self):
        w = self.window
        w.place(100, 50)
        x, y = w.x + 50, w.y + 2  # on the top edge
        edge = press()
        w.on_drag_begin(edge, x, y)
        self.assertEqual(edge.states, [nc.Gtk.EventSequenceState.CLAIMED])
        self.assertEqual(w.resizing[0], 'n')
        w.point_menu(w.x + 30, w.y + 30)
        before = self.menu()
        hold = press()
        w.on_long_press(hold, x, y)
        self.assertEqual(hold.states, [])
        self.assertEqual(self.menu(), before)
        self.assertEqual(self.shell.modes, [])
        self.assertEqual(w.resizing[0], 'n')
        w.on_drag_end(None, 0, 0)


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
            self.assertEqual(self.app.on_command_line(self.app, NS(get_options_dict=lambda: options)), 0)
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
        desktop.load_from_file(str(ROOT / 'data' / f'{nc.APP_ID}.desktop'), nc.GLib.KeyFileFlags.NONE)
        actions = desktop.get_string_list('Desktop Entry', 'Actions')
        self.assertEqual(actions, [name for name, _ in nc.NowCaption.LAUNCH_OPTIONS])
        names = {}
        for action in actions:
            group = f'Desktop Action {action}'
            # Flatpak exports this as `flatpak run ... --command=nowcaption <app id> --transcript`, and
            # AppImage integration replaces only the program, so the option always arrives
            self.assertEqual(desktop.get_string(group, 'Exec'), f'nowcaption --{action}')
            self.assertTrue(self.app.has_action(action))
            names[action] = desktop.get_string(group, 'Name')
        self.assertEqual(names, {'transcript': 'Open transcript', 'options': 'More options', 'quit': 'Quit'})


# The NowCaption already running, without a window, models or audio: it prints what it is asked
# to do, then launches NowCaption again as the desktop file's actions do.
RUNNING = """
import subprocess, sys, threading
from nowcaption import app as nc

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
    nc.Adw.Application.quit(self)

nc.NowCaption.on_activate = started
nc.NowCaption.open_transcript = lambda self: print('transcript', flush=True)
nc.NowCaption.open_options = lambda self: print('options', flush=True)
nc.NowCaption.request_quit = quit
app = nc.NowCaption()
app.fullscreen_watch.bus = None
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


if __name__ == '__main__':
    unittest.main()
