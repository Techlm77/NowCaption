"""Keyboard shortcuts that work in any app, through the desktop's GlobalShortcuts portal.

Asynchronous, on the GTK thread, through portal.Portal's requests; where the desktop has no such
portal the callbacks say so and NowCaption works without the keys. Knows nothing of the app.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib

from .portal import BUS, CANCELLED, PATH, _tokens, unpack

if TYPE_CHECKING:  # for annotations only
    from .portal import Portal

LOG = logging.getLogger(__name__)

SHORTCUTS = 'org.freedesktop.portal.GlobalShortcuts'


class GlobalShortcuts:
    """Keys that reach NowCaption in any app, through the desktop's GlobalShortcuts portal (KDE
    Plasma 6, GNOME 48 and newer, Hyprland). The desktop owns the keys: the user sees and changes
    them in its settings, and the first set_up() shows its dialog to confirm them.

    SHORTCUTS is (id, description, preferred keys). on_activated(id) runs when one is pressed, and
    on_changed() whenever bound (the keys the desktop gave them), available or error changes.
    """

    SHORTCUTS = (('show-hide', 'Show or hide the captions', 'CTRL+ALT+C'),
                 ('pause', 'Pause or resume the captions', 'CTRL+ALT+P'))

    def __init__(self, portal: Portal, on_activated: Callable[[str], None],
                 on_changed: Callable[[], None] | None = None) -> None:
        self.portal = portal
        self.on_activated, self.on_changed = on_activated, on_changed or (lambda: None)
        self.session = None
        self.supported = None  # unknown until start(); then whether the desktop has the portal
        self.can_configure = False  # the desktop can show its own shortcut settings (version 2)
        self.bound = {}  # id -> what the desktop says triggers it ("Ctrl+Alt+C"), once bound
        self.error = ''
        self.subscriptions = []

    @property
    def available(self) -> bool:
        """Whether the keys can work: the desktop has the portal and a session is open."""
        return bool(self.supported and self.session)

    def start(self, bind: bool = False) -> None:
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

    def set_up(self) -> None:
        """Ask the desktop for the keys now (it may show a dialog to confirm or change them)."""
        if self.session:
            self.bind()
        else:
            self.start(bind=True)

    def configure(self) -> bool:
        """Open the desktop's own settings for NowCaption's keys (GlobalShortcuts version 2).
        Returns False when the desktop cannot."""
        if not (self.session and self.can_configure):
            return False
        self.portal.bus.call(BUS, PATH, SHORTCUTS, 'ConfigureShortcuts',
                             GLib.Variant('(osa{sv})', (self.session, '', {})), None,
                             Gio.DBusCallFlags.NONE, -1, None, None)
        return True

    def create_session(self, bind: bool) -> None:
        """Open a session, then listen for the keys and list them (see list_shortcuts)."""
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

    def listen(self) -> None:
        """Follow the portal's Activated and ShortcutsChanged signals."""
        bus = self.portal.bus
        for name, handler in (('Activated', self.activated), ('ShortcutsChanged', self.changed)):
            self.subscriptions.append(bus.signal_subscribe(BUS, SHORTCUTS, name, PATH, None,
                                                           Gio.DBusSignalFlags.NONE, handler))

    def activated(self, bus, sender, path, iface, signal, params):
        """The Activated signal: a key was pressed."""
        session, shortcut = params.unpack()[:2]
        if session == self.session:
            self.on_activated(shortcut)

    def changed(self, bus, sender, path, iface, signal, params):
        """The ShortcutsChanged signal: the user changed the keys in the desktop's settings."""
        session, shortcuts = params.unpack()
        if session == self.session:
            self.remember(shortcuts)

    def remember(self, shortcuts: Iterable[tuple[str, dict]]) -> None:
        """Keep what the desktop says triggers each of NowCaption's shortcuts, as (id, details)."""
        known = {s[0] for s in self.SHORTCUTS}
        self.bound = {sid: str(info.get('trigger_description', '') or '') for sid, info in shortcuts
                      if sid in known}
        self.on_changed()

    def list_shortcuts(self, bind: bool) -> None:
        """Ask the desktop which keys the shortcuts have; with bind, ask for keys if any has none."""
        def listed(code, results):
            if code == 0:
                self.remember(unpack(results).get('shortcuts', []))
            if bind and set(self.bound) != {s[0] for s in self.SHORTCUTS}:
                self.bind()
            else:
                self.on_changed()
        self.portal.request(SHORTCUTS, 'ListShortcuts', lambda options: GLib.Variant(
            '(oa{sv})', (self.session, options)), listed)

    def bind(self) -> None:
        """Ask the desktop to set the keys up (it may show its dialog to confirm or change them)."""
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

    def stop(self) -> None:
        """Close the session: the desktop stops sending the keys to NowCaption."""
        for sub in self.subscriptions:
            self.portal.bus.signal_unsubscribe(sub)
        self.subscriptions = []
        if self.session:
            self.portal.bus.call(BUS, self.session, 'org.freedesktop.portal.Session', 'Close', None, None,
                                 Gio.DBusCallFlags.NONE, -1, None, None)
            self.session = None
        self.bound = {}
