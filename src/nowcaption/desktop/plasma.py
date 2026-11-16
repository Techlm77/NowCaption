"""KDE Plasma integration over D-Bus: a KWin script that reports a fullscreen window on the captions'
screen, and turning a floating panel's floating off while the captions are docked on its edge.
Knows nothing of the app beyond its ID and its settings."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib

if TYPE_CHECKING:  # for annotations only
    from ..settings import Settings

LOG = logging.getLogger(__name__)


# On KDE Plasma, docked captions float over a fullscreen app on their screen (which hides the
# panels they line up with), and dock again when it leaves fullscreen. Wayland does not tell an
# app about other windows, so a small KWin script, loaded while the captions are docked, reports
# whether the active window on the captions' screen is fullscreen.
FULLSCREEN_PATH = '/io/github/Techlm77/NowCaption/Fullscreen'
FULLSCREEN_IFACE = 'io.github.Techlm77.NowCaption.Fullscreen'
FULLSCREEN_XML = f"""<node><interface name="{FULLSCREEN_IFACE}">
  <method name="Changed"><arg type="b" name="fullscreen" direction="in"/></method>
</interface></node>"""
KWIN_SCRIPT = """// NowCaption: whether the active window on the captions' screen is fullscreen.
// KWin 6 has windows that name their output; KWin 5.27 (Plasma 5) has clients and only screen
// numbers, so there the screen is found by its area. var and plain functions suit both engines.
var OUTPUT = %s;  // the screen's connector name, "" for any screen
var AREA = %s;  // the screen's x, y, width and height, or null
var last = null;
function windows() { return workspace.windowList ? workspace.windowList() : workspace.clientList(); }
function onScreen(w) {
    if (OUTPUT === "") return true;
    if (w.output) return w.output.name === OUTPUT;
    var g = w.frameGeometry;
    if (!AREA || !g) return true;
    var x = g.x + g.width / 2, y = g.y + g.height / 2;
    return x >= AREA[0] && x < AREA[0] + AREA[2] && y >= AREA[1] && y < AREA[1] + AREA[3];
}
function update() {
    var w = workspace.windowList ? workspace.activeWindow : workspace.activeClient;
    var full = !!(w && w.fullScreen && onScreen(w));
    if (full !== last) {
        last = full;
        callDBus(%s, %s, %s, "Changed", full);
    }
}
function watch(w) { if (w) w.fullScreenChanged.connect(update); }
windows().forEach(watch);
(workspace.windowAdded || workspace.clientAdded).connect(function (w) { watch(w); update(); });
(workspace.windowRemoved || workspace.clientRemoved).connect(update);
(workspace.windowActivated || workspace.clientActivated).connect(update);
update();
"""


class FullscreenWatch:
    """Loads and unloads NowCaption's KWin script over D-Bus. Nothing happens where KWin is not
    running: the calls just fail."""
    NAME = 'nowcaption-fullscreen'

    def __init__(self, app_id: str) -> None:
        self.app_id, self.output, self.bus = app_id, None, None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            pass

    def call(self, method: str, args: tuple[str, tuple] | None, done: Callable[[], None] | None = None,
             sync: bool = False) -> None:
        """Call KWin's scripting method with args (a GLib.Variant type string and its value), then
        done() once it worked. sync waits for the answer, at most half a second (as the app quits)."""
        if not self.bus:
            return
        params = GLib.Variant(args[0], args[1]) if args else None
        if sync:
            try:
                self.bus.call_sync('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting', method, params,
                                   None, Gio.DBusCallFlags.NO_AUTO_START, 500, None)
            except GLib.Error:
                pass
            return

        def finished(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error:
                return
            if done:
                done()
        self.bus.call('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting', method, params,
                      None, Gio.DBusCallFlags.NO_AUTO_START, 2000, None, finished)

    def start(self, output: str, area: tuple[int, int, int, int] | None = None) -> None:
        """Watch the screen with this connector name ('' for any), whose area (x, y, width,
        height) finds it on KWin 5, which names no screens."""
        if (output, area) == self.output or not self.bus:
            return
        self.output = output, area
        path = Path(GLib.get_user_data_dir()) / 'nowcaption' / 'kwin-fullscreen.js'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(KWIN_SCRIPT % tuple(map(json.dumps, (output, area, self.app_id, FULLSCREEN_PATH,
                                                                   FULLSCREEN_IFACE))))
        except OSError as e:
            LOG.warning('Could not write the KWin script: %s', e)
            return
        self.call('unloadScript', ('(s)', (self.NAME,)), lambda: self.call(
            'loadScript', ('(ss)', (str(path), self.NAME)), lambda: self.call('start', None)))

    def stop(self, sync: bool = False) -> None:
        """Stop watching: unload the script, if it is loaded. sync waits for KWin (as the app quits)."""
        if self.output is not None:
            self.output = None
            self.call('unloadScript', ('(s)', (self.NAME,)), sync=sync)


# On KDE Plasma a floating panel stops floating while a window touches it, but Plasma only counts
# app windows. Docked on the panel's edge, the captions touch it and maximised windows end at the
# captions instead, so the panel would float on as if nothing touched it. While the captions are
# docked on the edge and screen of a floating panel, NowCaption turns that panel's floating off
# through Plasma's desktop scripting, and on again when they leave that edge or the app quits. The
# panels it changed are kept in the settings ('unfloated'), so after a crash the next start puts
# them back. Plasma 5's scripting has no floating to change: there nothing happens.
PANELS_SCRIPT = """// NowCaption: the panels the docked captions touch do not float.
var OURS = %s;  // the panels NowCaption turned floating off for before
var EDGE = %s;  // the edge the captions are docked on, "" when they are not
var OUTPUT = %s;  // their screen's connector name, "" for any screen
var screen = OUTPUT !== "" && typeof screenForConnector === "function" ? screenForConnector(OUTPUT) : -1;
var now = [];
panels().forEach(function (p) {
    if (typeof p.floating !== "boolean") return;
    var touched = EDGE !== "" && p.location === EDGE && (OUTPUT === "" || p.screen === screen);
    var ours = OURS.indexOf(p.id) >= 0;
    if (touched && (p.floating || ours)) {
        p.floating = false;
        now.push(p.id);
    } else if (ours) {
        p.floating = true;
    }
});
print(JSON.stringify(now));
"""


class PanelFloat:
    """Runs PANELS_SCRIPT in Plasma when the docked edge changes. The calls are synchronous but
    short (a hung Plasma costs at most TIMEOUT_MS once); where Plasma is not running they fail at
    once and nothing changes."""
    TIMEOUT_MS = 500

    def __init__(self, settings: Settings) -> None:
        self.settings, self.want, self.bus = settings, False, None  # False: not told yet
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            pass

    def ours(self) -> list[int]:
        """The ids of the panels NowCaption turned floating off for (kept in the settings)."""
        return [int(i) for i in self.settings.get('unfloated').split(',') if i.strip().isdigit()]

    def dock(self, edge: str | None, output: str = '') -> None:
        """The captions are docked on edge ('top', 'bottom', 'left' or 'right') of the screen with
        this connector name ('' for any), or not docked (None)."""
        want = (edge, output or '') if edge else None
        if want == self.want:
            return
        self.want = want
        if want or self.ours():
            self.run(*(want or ('', '')))

    def stop(self) -> None:
        """The app quits: the panels float again."""
        self.dock(None)

    def run(self, edge: str, output: str) -> None:
        """Run PANELS_SCRIPT for this edge ('' when not docked) and screen, and keep the ids of the
        panels whose floating it turned off."""
        if not self.bus:
            return
        script = PANELS_SCRIPT % tuple(map(json.dumps, (self.ours(), edge, output)))
        try:
            reply = self.bus.call_sync('org.kde.plasmashell', '/PlasmaShell', 'org.kde.PlasmaShell',
                                       'evaluateScript', GLib.Variant('(s)', (script,)), GLib.VariantType('(s)'),
                                       Gio.DBusCallFlags.NO_AUTO_START, self.TIMEOUT_MS, None)
            now = json.loads(reply.unpack()[0].strip().splitlines()[-1])
        except (GLib.Error, ValueError, IndexError) as e:
            LOG.debug('Plasma panels left as they are: %s', e)
            return
        if isinstance(now, list):
            self.settings.set('unfloated', ','.join(str(i) for i in now if isinstance(i, int)))
