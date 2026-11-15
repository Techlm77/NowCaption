"""The desktop's portals for what a sandboxed app may not do itself: keyboard shortcuts that work
in any app (GlobalShortcuts), and starting when the user logs in (Background).

Both answer through a Request object's Response signal. Everything here is asynchronous and runs
on the GTK thread; when a portal is missing (an older desktop, or none running) the callbacks say
so and NowCaption works as before. Outside the Flatpak, starting at login writes an autostart file
instead, which the Background portal does not do for apps that are not sandboxed.
"""
import itertools
import logging
import os
import sys
from pathlib import Path

from gi.repository import Gio, GLib

LOG = logging.getLogger(__name__)

BUS = 'org.freedesktop.portal.Desktop'
PATH = '/org/freedesktop/portal/desktop'
REQUEST = 'org.freedesktop.portal.Request'
SHORTCUTS = 'org.freedesktop.portal.GlobalShortcuts'
BACKGROUND = 'org.freedesktop.portal.Background'
REGISTRY = 'org.freedesktop.host.portal.Registry'
CANCELLED = 1  # a Response code: the user dismissed the portal's dialog
_tokens = itertools.count(1)


def in_flatpak():
    return os.path.exists('/.flatpak-info')


def unpack(results):
    """A Response's a{sv} as plain Python values."""
    return results.unpack() if isinstance(results, GLib.Variant) else dict(results or {})


class Portal:
    """Calls to the portal that answer later through a Request object."""

    def __init__(self, connection, app_id):
        self.bus, self.app_id = connection, app_id
        self.registered = in_flatpak()  # the sandbox tells the portal who we are

    def register(self):
        """Outside a sandbox the portal learns the app id only if told first (xdg-desktop-portal
        1.19 and newer); older portals do not have this and guess from the process."""
        if self.registered:
            return
        self.registered = True
        try:
            self.bus.call_sync(BUS, PATH, REGISTRY, 'Register', GLib.Variant('(sa{sv})', (self.app_id, {})),
                               None, Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error as e:
            LOG.info('The portal has no app registry: %s', e.message)

    def request(self, interface, method, make_args, on_response):
        """Call method with make_args(options), where options already holds a handle_token, and
        pass on_response(code, results) the Response: 0 done, 1 cancelled, 2 failed (also when the
        call itself fails, with results {'error': message})."""
        self.register()
        token = f'nowcaption{os.getpid()}_{next(_tokens)}'
        sender = self.bus.get_unique_name()[1:].replace('.', '_')
        expected = f'{PATH}/request/{sender}/{token}'
        state = {'sub': None, 'done': False}

        def finish(code, results):
            if state['sub'] is not None:
                self.bus.signal_unsubscribe(state['sub'])
                state['sub'] = None
            if not state['done']:
                state['done'] = True
                on_response(code, results)

        def on_signal(bus, sender_name, path, iface, signal, params):
            code, results = params.unpack()
            finish(code, results)

        def subscribe(path):
            if state['sub'] is not None:
                self.bus.signal_unsubscribe(state['sub'])
            state['sub'] = self.bus.signal_subscribe(BUS, REQUEST, 'Response', path, None,
                                                     Gio.DBusSignalFlags.NONE, on_signal)

        def called(bus, result):
            try:
                handle = bus.call_finish(result).unpack()[0]
            except GLib.Error as e:
                finish(2, {'error': e.message})
                return
            if handle != expected and not state['done']:  # portals before 0.9 chose their own path
                subscribe(handle)

        subscribe(expected)  # before the call, so a quick Response is not missed
        options = {'handle_token': GLib.Variant('s', token)}
        self.bus.call(BUS, PATH, interface, method, make_args(options), GLib.VariantType('(o)'),
                      Gio.DBusCallFlags.NONE, -1, None, called)

    def version(self, interface, done):
        """done(version), 0 when the portal does not have this interface."""
        def got(bus, result):
            try:
                done(bus.call_finish(result).unpack()[0])
            except GLib.Error:
                done(0)
        self.bus.call(BUS, PATH, 'org.freedesktop.DBus.Properties', 'Get',
                      GLib.Variant('(ss)', (interface, 'version')), GLib.VariantType('(v)'),
                      Gio.DBusCallFlags.NONE, 3000, None, got)


class GlobalShortcuts:
    """Keys that reach NowCaption in any app, through the desktop's GlobalShortcuts portal (KDE
    Plasma 6, GNOME 48 and newer, Hyprland). The desktop owns the keys: the user sees and changes
    them in its settings, and the first set_up() shows its dialog to confirm them.

    SHORTCUTS is (id, description, preferred keys). on_activated(id) runs when one is pressed, and
    on_changed() whenever what triggers() and available say changes.
    """

    SHORTCUTS = (('show-hide', 'Show or hide the captions', 'CTRL+ALT+C'),
                 ('pause', 'Pause or resume the captions', 'CTRL+ALT+P'))

    def __init__(self, portal, on_activated, on_changed=None):
        self.portal = portal
        self.on_activated, self.on_changed = on_activated, on_changed or (lambda: None)
        self.session = None
        self.supported = None  # unknown until start(); then whether the desktop has the portal
        self.can_configure = False  # the desktop can show its own shortcut settings (version 2)
        self.bound = {}  # id -> what the desktop says triggers it ("Ctrl+Alt+C"), once bound
        self.error = ''
        self.subscriptions = []

    @property
    def available(self):
        return bool(self.supported and self.session)

    def start(self, bind=False):
        """Open a session and listen for the keys; with bind, also ask the desktop to set up the
        ones it has not got yet (showing its dialog), as set_up() does."""
        if self.supported is False:
            return self.on_changed()

        def checked(version):
            self.supported = version > 0
            self.can_configure = version >= 2
            if not self.supported:
                self.error = 'This desktop does not offer keyboard shortcuts to apps.'
                return self.on_changed()
            self.create_session(bind)
        if self.supported is None:
            self.portal.version(SHORTCUTS, checked)
        elif self.session:
            self.list_shortcuts(bind)
        else:
            self.create_session(bind)

    def set_up(self):
        """Ask the desktop for the keys now (it may show a dialog to confirm or change them)."""
        if self.session:
            self.bind()
        else:
            self.start(bind=True)

    def configure(self):
        """Open the desktop's own settings for NowCaption's keys (GlobalShortcuts version 2)."""
        if not (self.session and self.can_configure):
            return False
        self.portal.bus.call(BUS, PATH, SHORTCUTS, 'ConfigureShortcuts',
                             GLib.Variant('(osa{sv})', (self.session, '', {})), None,
                             Gio.DBusCallFlags.NONE, -1, None, None)
        return True

    def create_session(self, bind):
        def created(code, results):
            results = unpack(results)
            if code != 0 or 'session_handle' not in results:
                self.error = results.get('error') or 'The desktop did not open a shortcut session.'
                LOG.warning('Global shortcuts unavailable: %s', self.error)
                return self.on_changed()
            self.session = results['session_handle']
            self.listen()
            self.list_shortcuts(bind)
        token = f'nowcaption_session_{os.getpid()}_{next(_tokens)}'
        self.portal.request(SHORTCUTS, 'CreateSession', lambda options: GLib.Variant(
            '(a{sv})', ({**options, 'session_handle_token': GLib.Variant('s', token)},)), created)

    def listen(self):
        bus = self.portal.bus
        for name, handler in (('Activated', self.activated), ('ShortcutsChanged', self.changed)):
            self.subscriptions.append(bus.signal_subscribe(BUS, SHORTCUTS, name, PATH, None,
                                                           Gio.DBusSignalFlags.NONE, handler))

    def activated(self, bus, sender, path, iface, signal, params):
        session, shortcut = params.unpack()[:2]
        if session == self.session:
            self.on_activated(shortcut)

    def changed(self, bus, sender, path, iface, signal, params):
        session, shortcuts = params.unpack()
        if session == self.session:
            self.remember(shortcuts)

    def remember(self, shortcuts):
        known = {s[0] for s in self.SHORTCUTS}
        self.bound = {sid: str(info.get('trigger_description', '') or '') for sid, info in shortcuts
                      if sid in known}
        self.on_changed()

    def list_shortcuts(self, bind):
        def listed(code, results):
            if code == 0:
                self.remember(unpack(results).get('shortcuts', []))
            if bind and set(self.bound) != {s[0] for s in self.SHORTCUTS}:
                self.bind()
            else:
                self.on_changed()
        self.portal.request(SHORTCUTS, 'ListShortcuts', lambda options: GLib.Variant(
            '(oa{sv})', (self.session, options)), listed)

    def bind(self):
        def bound(code, results):
            if code == 0:
                self.error = ''
                self.remember(unpack(results).get('shortcuts', []))
            else:
                if code != CANCELLED:
                    self.error = unpack(results).get('error') or 'The desktop did not set the keys up.'
                self.on_changed()
        shortcuts = [(sid, {'description': GLib.Variant('s', text), 'preferred_trigger': GLib.Variant('s', keys)})
                     for sid, text, keys in self.SHORTCUTS]
        self.portal.request(SHORTCUTS, 'BindShortcuts', lambda options: GLib.Variant(
            '(oa(sa{sv})sa{sv})', (self.session, shortcuts, '', options)), bound)

    def stop(self):
        """Close the session: the desktop stops sending the keys to NowCaption."""
        for sub in self.subscriptions:
            self.portal.bus.signal_unsubscribe(sub)
        self.subscriptions = []
        if self.session:
            self.portal.bus.call(BUS, self.session, 'org.freedesktop.portal.Session', 'Close', None, None,
                                 Gio.DBusCallFlags.NONE, -1, None, None)
            self.session = None
        self.bound = {}


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
    src = str(Path(__file__).resolve().parents[1])
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


def portal_for(app):
    """A Portal on the app's session bus connection, or None without one (tests, no D-Bus)."""
    bus = app.get_dbus_connection() if app else None
    return Portal(bus, app.get_application_id()) if bus else None

