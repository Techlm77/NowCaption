#!/usr/bin/env python3
"""Which Wayland display the Flatpak's launcher (flatpak/nowcaption.sh) gives NowCaption.

Flatpak sets WAYLAND_DISPLAY itself, and then the launcher must change nothing. Hyprland and Sway
hide docking from Flatpak's own (sandboxed) Wayland socket, so README.md offers an opt-in
override that turns that socket off and shares the desktop's: Flatpak then sets no
WAYLAND_DISPLAY, and the launcher uses the one shared socket. Once that override is undone,
Flatpak's link to its own socket can't come back until the next login, and the launcher
connects to the socket the link would point to. gtk4-layer-shell is preloaded only with a
Wayland display, so the display must be chosen first.

The launcher runs for real, with sh, with /app and /run/flatpak moved to a temporary folder,
real Unix sockets in a fake XDG_RUNTIME_DIR and a fake python3 that saves its environment.
Standard library only.
"""
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # the repository
LAYER_SHELL = 'app/lib/libgtk4-layer-shell.so.0'


def read_env(path):
    """The environment the fake python3 saved with env -0."""
    return dict(item.split('=', 1) for item in path.read_text().split('\0') if item)


class FlatpakLauncherTests(unittest.TestCase):
    """flatpak/nowcaption.sh in a fake sandbox: <root>/app, <root>/run-flatpak (the sandbox's
    /run/flatpak, where Flatpak mounts its own Wayland socket) and <root>/xdg-run."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.sockets = []
        self.addCleanup(lambda: [s.close() for s in self.sockets])
        text = (ROOT / 'flatpak/nowcaption.sh').read_text()
        text = text.replace('/run/flatpak/', f'{self.root}/run-flatpak/')
        self.launcher = self.root / 'nowcaption.sh'
        self.launcher.write_text(text.replace('/app/', f'{self.root}/app/'))
        (self.root / LAYER_SHELL).parent.mkdir(parents=True)
        (self.root / LAYER_SHELL).touch()
        python3 = self.root / 'bin/python3'
        python3.parent.mkdir()
        python3.write_text('#!/bin/sh\nexec env -0 > "$FAKE_OUT"\n')
        python3.chmod(0o755)
        self.runtime = self.root / 'xdg-run'
        self.runtime.mkdir()
        (self.root / 'run-flatpak').mkdir()

    def listen(self, path):
        """A real Unix socket at path (bound by its name from its folder: a full temporary path
        can be longer than a socket's address may be)."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sockets.append(sock)
        here = os.getcwd()
        os.chdir(path.parent)
        try:
            sock.bind(path.name)
        finally:
            os.chdir(here)

    def launch(self, wayland_display=None):
        """The launcher's environment for python3, started with WAYLAND_DISPLAY as given."""
        out = self.root / 'env'
        env = {'PATH': f'{self.root}/bin:{os.environ["PATH"]}', 'HOME': str(self.root),
               'FAKE_OUT': str(out), 'XDG_RUNTIME_DIR': str(self.runtime)}
        if wayland_display is not None:
            env['WAYLAND_DISPLAY'] = wayland_display
        # The loader complains about the fake gtk4-layer-shell (an empty file) when it is preloaded
        run = subprocess.run(['sh', str(self.launcher), '--flag'], env=env, capture_output=True,
                             text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        return read_env(out)

    def assert_wayland(self, env, display):
        self.assertEqual(env.get('WAYLAND_DISPLAY'), display)
        self.assertEqual(env.get('LD_PRELOAD'), str(self.root / LAYER_SHELL))

    def assert_no_wayland(self, env):
        self.assertFalse(env.get('WAYLAND_DISPLAY'), env.get('WAYLAND_DISPLAY'))
        self.assertNotIn('LD_PRELOAD', env)

    def test_the_display_flatpak_sets_is_kept(self):
        """Flatpak's own socket (as a socket, or the link to /run/flatpak it really is) is used
        as Flatpak named it, even with other sockets in reach."""
        self.listen(self.runtime / 'wayland-0')
        self.listen(self.runtime / 'wayland-1')
        self.listen(self.root / 'run-flatpak/wayland-0')
        self.assert_wayland(self.launch('wayland-0'), 'wayland-0')
        (self.runtime / 'wayland-0').unlink()
        (self.runtime / 'wayland-0').symlink_to(self.root / 'run-flatpak/wayland-0')
        self.assert_wayland(self.launch('wayland-0'), 'wayland-0')

    def test_the_only_shared_socket_is_used(self):
        """With Flatpak's socket turned off and the desktop's shared, WAYLAND_DISPLAY names it,
        before gtk4-layer-shell is preloaded; its lock file is not a second display."""
        self.listen(self.runtime / 'wayland-1')
        (self.runtime / 'wayland-1.lock').touch()
        self.assert_wayland(self.launch(), 'wayland-1')
        self.assert_wayland(self.launch(''), 'wayland-1')

    def test_no_display_without_exactly_one_socket(self):
        """No socket (only files named like one), or two to choose between: no Wayland display
        is made up, and nothing is preloaded."""
        (self.runtime / 'wayland-0.lock').touch()
        (self.runtime / 'wayland-2').touch()
        self.assert_no_wayland(self.launch())
        self.listen(self.runtime / 'wayland-0')
        self.listen(self.runtime / 'wayland-1')
        self.assert_no_wayland(self.launch())
        self.assert_no_wayland(self.launch(''))

    def test_flatpak_socket_after_the_override_is_undone(self):
        """Where the shared socket was mounted, an empty file is left until the next login, so
        Flatpak's link can't be made: the launcher connects to Flatpak's socket directly."""
        (self.runtime / 'wayland-1').touch()
        self.listen(self.root / 'run-flatpak/wayland-1')
        self.assert_wayland(self.launch('wayland-1'), f'{self.root}/run-flatpak/wayland-1')


if __name__ == '__main__':
    unittest.main()
