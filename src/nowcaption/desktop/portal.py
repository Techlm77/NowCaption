"""The desktop's portals for what a sandboxed app may not do itself (shortcuts.py, autostart.py):
calls that answer through a Request object's Response signal. Everything here is asynchronous and
runs on the GTK thread; when a portal is missing (an older desktop, or none running) the callbacks
say so and NowCaption works as before. Knows nothing of the app beyond its ID.
"""
import itertools
import logging
import os

from gi.repository import Gio, GLib

LOG = logging.getLogger(__name__)

BUS = 'org.freedesktop.portal.Desktop'
PATH = '/org/freedesktop/portal/desktop'
REQUEST = 'org.freedesktop.portal.Request'
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


def portal_for(app):
    """A Portal on the app's session bus connection, or None without one (tests, no D-Bus)."""
    bus = app.get_dbus_connection() if app else None
    return Portal(bus, app.get_application_id()) if bus else None

