#!/usr/bin/env python3
"""Docked captions on the edge of a floating Plasma panel: the panel stops floating while they are
there, as it does for a window that touches it, and floats again when they leave or NowCaption
quits, also after a crash.

A fake session bus stands in for Plasma, and PANELS_SCRIPT runs in Node.js against fake panels
(skipped where Node.js is not installed). No GTK window, audio server or models.
"""
import json
import shutil
import subprocess
import unittest

from nowcaption import app as nc
from nowcaption.app import GLib


class FakeSettings:
    def __init__(self, unfloated=''):
        self.values = {'unfloated': unfloated}

    def get(self, key):
        return self.values[key]

    def set(self, key, value):
        self.values[key] = value


class FakeBus:
    """Plasma's evaluateScript: records each script and answers with what it would print."""
    def __init__(self, reply='[]', error=None):
        self.scripts, self.reply, self.error = [], reply, error

    def call_sync(self, name, path, iface, method, params, reply_type, flags, timeout, cancellable):
        self.scripts.append((name, path, iface, method, params.unpack()[0]))
        if self.error:
            raise self.error
        return GLib.Variant('(s)', (self.reply,))


def panel_float(unfloated='', reply='[]', error=None):
    p = nc.PanelFloat(FakeSettings(unfloated))
    p.bus = FakeBus(reply, error)
    return p


def values(script):
    """OURS, EDGE and OUTPUT as the script was given them."""
    lines = {line.split(' = ')[0][4:]: line.split(' = ')[1].split(';')[0]
             for line in script.splitlines() if line.startswith('var ') and ' = ' in line}
    return json.loads(lines['OURS']), json.loads(lines['EDGE']), json.loads(lines['OUTPUT'])


class PanelFloatTests(unittest.TestCase):
    def test_docking_asks_plasma_and_remembers_the_panels_it_changed(self):
        p = panel_float(reply='[98]')
        p.dock('bottom', 'DP-1')
        name, path, iface, method, script = p.bus.scripts[0]
        self.assertEqual((name, path, iface, method),
                         ('org.kde.plasmashell', '/PlasmaShell', 'org.kde.PlasmaShell', 'evaluateScript'))
        self.assertEqual(values(script), ([], 'bottom', 'DP-1'))
        self.assertEqual(p.settings.get('unfloated'), '98')

    def test_the_same_dock_again_asks_nothing(self):
        p = panel_float(reply='[98]')
        p.dock('bottom', 'DP-1')
        p.dock('bottom', 'DP-1')
        self.assertEqual(len(p.bus.scripts), 1)

    def test_leaving_the_edge_floats_the_remembered_panels_again(self):
        p = panel_float(reply='[98]')
        p.dock('bottom', 'DP-1')
        p.bus.reply = '[]'
        p.dock('top', 'DP-1')
        self.assertEqual(values(p.bus.scripts[1][4]), ([98], 'top', 'DP-1'))
        self.assertEqual(p.settings.get('unfloated'), '')

    def test_floating_captions_with_nothing_remembered_ask_nothing(self):
        p = panel_float()
        p.dock(None)
        p.stop()
        self.assertEqual(p.bus.scripts, [])

    def test_the_first_start_after_a_crash_floats_remembered_panels_again(self):
        p = panel_float(unfloated='98,120')
        p.dock(None)
        self.assertEqual(values(p.bus.scripts[0][4]), ([98, 120], '', ''))
        self.assertEqual(p.settings.get('unfloated'), '')

    def test_quitting_floats_them_again(self):
        p = panel_float(reply='[98]')
        p.dock('left', 'HDMI-A-1')
        p.bus.reply = '[]'
        p.stop()
        self.assertEqual(values(p.bus.scripts[-1][4]), ([98], '', ''))
        self.assertEqual(p.settings.get('unfloated'), '')

    def test_without_plasma_nothing_changes(self):
        error = GLib.Error.new_literal(GLib.quark_from_string('g-dbus-error-quark'),
                                       'The name is not activatable', 2)
        p = panel_float(unfloated='98', error=error)
        p.dock('bottom', 'DP-1')
        p.stop()
        self.assertEqual(p.settings.get('unfloated'), '98')

    def test_a_reply_that_is_not_the_list_changes_nothing(self):
        for reply in ('', 'TypeError: panels is not a function', '{"98": true}'):
            p = panel_float(unfloated='98', reply=reply)
            p.dock('bottom', 'DP-1')
            self.assertEqual(p.settings.get('unfloated'), '98', reply)

    def test_without_a_session_bus_nothing_is_asked(self):
        p = panel_float(reply='[98]')
        p.bus = None
        p.dock('bottom', 'DP-1')
        self.assertEqual(p.settings.get('unfloated'), '')


@unittest.skipUnless(shutil.which('node'), 'Node.js runs the Plasma script')
class PanelsScriptTests(unittest.TestCase):
    """PANELS_SCRIPT in Node.js, with panels() and screenForConnector() as Plasma 6 has them."""
    SCREENS = {'DP-1': 0, 'DP-2': 1}

    def run_script(self, panels, ours=(), edge='', output='', plasma5=False):
        script = nc.PANELS_SCRIPT % tuple(map(json.dumps, (list(ours), edge, output)))
        harness = (
            f'const state = {json.dumps(panels)};\n'
            f'const PLASMA5 = {json.dumps(plasma5)};\n'
            'const printed = [];\n'
            'function print(s) { printed.push(s); }\n'
            'function panels() {\n'
            '  return state.map(function (s) {\n'
            '    const p = {id: s.id, location: s.location, screen: s.screen};\n'
            '    if (!PLASMA5) Object.defineProperty(p, "floating", {get: () => s.floating, set: v => { s.floating = v; }});\n'
            '    return p;\n'
            '  });\n'
            '}\n'
            + ('' if plasma5 else f'function screenForConnector(c) {{ const s = {json.dumps(self.SCREENS)}; return c in s ? s[c] : -1; }}\n')
            + script
            + '\nconsole.log(JSON.stringify({printed: printed, state: state}));\n')
        out = subprocess.run(['node', '-e', harness], capture_output=True, text=True, check=True).stdout
        result = json.loads(out)
        return json.loads(result['printed'][-1]), {s['id']: s['floating'] for s in result['state']}

    def panels(self):
        return [{'id': 1, 'location': 'bottom', 'screen': 0, 'floating': True},
                {'id': 2, 'location': 'bottom', 'screen': 1, 'floating': True},
                {'id': 3, 'location': 'top', 'screen': 0, 'floating': True},
                {'id': 4, 'location': 'bottom', 'screen': 0, 'floating': False}]

    def test_only_floating_panels_on_the_docked_edge_and_screen_stop_floating(self):
        now, floating = self.run_script(self.panels(), edge='bottom', output='DP-1')
        self.assertEqual(now, [1])
        self.assertEqual(floating, {1: False, 2: True, 3: True, 4: False})

    def test_a_panel_the_user_keeps_from_floating_is_left_alone(self):
        now, floating = self.run_script(self.panels(), edge='bottom', output='DP-1')
        self.assertNotIn(4, now)
        now, floating = self.run_script(self.panels(), ours=now, edge='', output='')
        self.assertEqual(now, [])
        self.assertFalse(floating[4])

    def test_panels_float_again_when_the_captions_leave(self):
        panels = self.panels()
        panels[0]['floating'] = False
        now, floating = self.run_script(panels, ours=[1], edge='top', output='DP-1')
        self.assertEqual(now, [3])
        self.assertEqual(floating, {1: True, 2: True, 3: False, 4: False})

    def test_docked_again_on_the_same_edge_the_panel_stays_ours(self):
        panels = self.panels()
        panels[0]['floating'] = False
        now, floating = self.run_script(panels, ours=[1], edge='bottom', output='DP-1')
        self.assertEqual(now, [1])
        self.assertFalse(floating[1])

    def test_without_a_screen_name_every_screen_counts(self):
        now, _ = self.run_script(self.panels(), edge='bottom', output='')
        self.assertEqual(now, [1, 2])

    def test_an_unknown_screen_touches_no_panel(self):
        now, floating = self.run_script(self.panels(), edge='bottom', output='HDMI-A-9')
        self.assertEqual(now, [])
        self.assertTrue(floating[1])

    def test_plasma_5_has_no_floating_to_change(self):
        now, _ = self.run_script(self.panels(), edge='bottom', output='DP-1', plasma5=True)
        self.assertEqual(now, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
