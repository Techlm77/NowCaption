"""Starting NowCaption when the user logs in: through the desktop's Background portal in the
Flatpak, or an autostart file outside it, which the Background portal does not do for apps that
are not sandboxed. Asynchronous, on the GTK thread. Knows nothing of the app's windows.
"""
import logging
import os
import sys
from pathlib import Path

from gi.repository import GLib

from .portal import CANCELLED, in_flatpak, unpack

LOG = logging.getLogger(__name__)

BACKGROUND = 'org.freedesktop.portal.Background'


def launch_command():
    """The command that starts this NowCaption: the AppImage, the installed `nowcaption`, or
    this checkout's Python."""
    appimage = os.environ.get('APPIMAGE')
    if appimage:
        return [appimage]
    from shutil import which
    installed = which('nowcaption')
    if installed:
        return [installed]
    src = str(Path(__file__).resolve().parents[2])
    return ['env', f'PYTHONPATH={src}', sys.executable, '-m', 'nowcaption']


def desktop_exec(args):
    """args quoted for a desktop file's Exec key (the Desktop Entry Specification's rules)."""
    def quote(arg):
        if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
            return arg
        escaped = ''.join('\\' + c if c in '"`$\\' else c for c in arg)
        return f'"{escaped}"'
    return ' '.join(quote(a) for a in args).replace('%', '%%')


class Autostart:
    """Start NowCaption when the user logs in. In the Flatpak, the Background portal adds it to
    the session (asking the user first on some desktops); outside it, a desktop file in
    $XDG_CONFIG_HOME/autostart does. done(on, message) reports the outcome: on is what now
    happens at login, and message says why when it is not what was asked for."""

    REASON = 'Start NowCaption when you log in, so captions are ready'

    def __init__(self, portal, app_id):
        self.portal, self.app_id = portal, app_id

    def path(self):
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
        return Path(base) / 'autostart' / f'{self.app_id}.desktop'

    def set(self, on, done):
        if in_flatpak():
            return self.ask_portal(on, done)
        try:
            self.write(on)
        except OSError as e:
            LOG.warning('Could not change the autostart file %s: %s', self.path(), e)
            return done(not on and self.path().exists(), f'Could not change it: {e.strerror or e}')
        done(on, '')

    def write(self, on):
        path = self.path()
        if not on:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text('[Desktop Entry]\nType=Application\nName=NowCaption\n'
                       'Comment=Live captions, started when you log in\n'
                       f'Exec={desktop_exec(launch_command())}\nIcon={self.app_id}\n'
                       'Terminal=false\nX-GNOME-Autostart-enabled=true\n')
        os.replace(tmp, path)

    def ask_portal(self, on, done):
        def answered(code, results):
            results = unpack(results)
            if code != 0:
                message = 'Not changed.' if code == CANCELLED else \
                    f"The desktop did not allow it: {results.get('error') or 'no reason given'}"
                return done(not on, message)
            granted = bool(results.get('autostart', on))
            done(granted, '' if granted == on else 'The desktop did not allow NowCaption to start at login.')
        self.portal.request(BACKGROUND, 'RequestBackground', lambda options: GLib.Variant('(sa{sv})', ('', {
            **options, 'reason': GLib.Variant('s', self.REASON), 'autostart': GLib.Variant('b', on),
            'commandline': GLib.Variant('as', ['nowcaption']), 'dbus-activatable': GLib.Variant('b', False)})),
            answered)
