#!/usr/bin/env python3
"""The caption window follows its screen's resolution and scale, and stops following a screen it
left (1.0.3). The window's own methods (support/app_loader.py) with fake screens; no GTK.
"""
import unittest
from types import SimpleNamespace as NS

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from support.app_loader import APP


class Monitor:
    def __init__(self, width):
        self.width, self.handlers = width, {}

    def get_geometry(self):
        return NS(width=self.width, height=600)

    def connect(self, signal, fn):
        handler = len(self.handlers) + 1
        self.handlers[handler] = (signal, fn)
        return handler

    def disconnect(self, handler):
        del self.handlers[handler]

    def notify(self, prop):
        for signal, fn in list(self.handlers.values()):
            if signal == f'notify::{prop}':
                fn(self, None)


class MonitorRegressions(unittest.TestCase):
    def window(self):
        w = APP['CaptionWindow']()
        w.monitor, w.box_width, w.layer = Monitor(3840), 1000, False
        w._monitor_handlers, w.restyles = [], 0
        w.restyle = lambda: setattr(w, 'restyles', w.restyles + 1)
        return w

    def test_resolution_and_scale_notifications_reflow_the_existing_monitor(self):
        w = self.window()
        w.watch_monitor()
        w.monitor.width = 800
        w.monitor.notify('geometry')
        self.assertEqual(w.box_width, 560)
        w.monitor.notify('scale-factor')
        self.assertEqual(w.restyles, 2)

    def test_switching_monitor_disconnects_old_notifications(self):
        w = self.window()
        w.watch_monitor()
        old = w.monitor
        new = Monitor(800)
        w.use_monitor(new)
        self.assertEqual(old.handlers, {})
        self.assertEqual(len(new.handlers), 2)
        old.width = 400
        old.notify('geometry')
        self.assertEqual(w.box_width, 560)
        model = Monitor(0)
        w._monitors, w._monitors_handler = model, model.connect('items-changed', lambda *a: None)
        w.disconnect_monitors()
        self.assertEqual(new.handlers, {})
        self.assertEqual(model.handlers, {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
