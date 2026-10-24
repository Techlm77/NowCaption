#!/usr/bin/env python3
"""1.1.16: More options can restart listening after a failure, and a closed More options
window is freed.

The application tests use test_app.py's fake window, worker and capture. The window tests
construct OptionsWindow on the application test_window.py registers, so they need a display
(xvfb-run or Broadway) like those tests.
"""
import gc
import unittest
import weakref
from unittest.mock import patch

import test_app
import test_window as gui

nc = gui.nc


@unittest.skipIf(test_app.nc is None, getattr(test_app, 'SKIP', ''))
class RestartListeningTests(unittest.TestCase):
    def make(self):
        a = test_app.AppTests.make(self)
        a.worker.restarts = 0
        a.worker.restart = lambda: setattr(a.worker, 'restarts', a.worker.restarts + 1)
        return a

    def test_after_the_model_fails_to_load_restart_loads_it_again(self):
        a = self.make()
        source = a.settings.get('source')
        a.on_worker('load-error', 'Could not load the speech model: no space left.')
        self.assertIn('To try again, open More options and press Restart.', a.window.statuses[-1])
        a.lookup_action('restart-listening').activate(None)
        self.assertEqual(a.worker.restarts, 1)
        self.assertEqual(a.window.statuses[-1], 'Retrying the speech model…')
        self.assertEqual(a.settings.get('source'), source)  # the same sound source

    def test_after_recognition_gives_up_restart_listens_again_with_new_retries(self):
        a = self.make()
        a.on_worker('ready', None)
        a.recognition_failures = 3  # the automatic retries are spent
        a.on_worker('error', (a.source_gen, 0, 'Speech recognition stopped: decoder failed'))
        self.assertIn('To try again, open More options and press Restart.', a.window.statuses[-1])
        self.assertIsNone(a.recognition_retry_id)
        a.lookup_action('restart-listening').activate(None)
        self.assertEqual(a.capture.starts, [1, 2])  # capture started again
        self.assertEqual(a.recognition_failures, 0)

    def test_restart_does_nothing_while_quitting(self):
        a = self.make()
        a.on_worker('ready', None)
        a.quit_requested = True  # waiting for a save to finish
        a.lookup_action('restart-listening').activate(None)
        self.assertEqual(a.capture.starts, [1])


@unittest.skipIf(nc is None, getattr(gui, 'SKIP', ''))
class OptionsWindowTests(unittest.TestCase):
    def setUp(self):
        self.app = gui.shared_app()
        self.app.open_options()
        self.window = self.app.options
        self.addCleanup(lambda: self.app.options and self.app.options.close())
        for name, default in (('word-effect', 'fade'), ('word-speed', 'normal'), ('source', 'desktop')):
            self.addCleanup(self.app.lookup_action(name).change_state, nc.GLib.Variant('s', default))
            self.addCleanup(self.app.settings.set, name.replace('-', '_'), default)

    def find(self, widget, kind):
        child = widget.get_first_child()
        while child:
            if isinstance(child, kind):
                yield child
            yield from self.find(child, kind)
            child = child.get_next_sibling()

    def checks(self):
        return {c.get_action_target_value().get_string(): c for c in self.find(self.window, nc.Gtk.CheckButton)}

    def settle(self):
        context = nc.GLib.MainContext.default()
        for _ in range(50):
            while context.pending():
                context.iteration(False)

    def test_restart_sits_under_listen_to_and_listens_again_at_once_without_apply(self):
        [row] = [row for row in self.find(self.window, nc.Adw.ActionRow) if row.get_title() == 'Start again']
        self.assertEqual(row.get_ancestor(nc.Adw.PreferencesGroup).get_title(), 'Listen to')
        [restart] = [b for b in self.find(row, nc.Gtk.Button) if b.get_label() == 'Restart']
        self.checks()['mic'].activate()  # a choice that waits for Apply
        with patch.object(self.app, 'ready', True), patch.object(self.app, 'start_capture') as start:
            restart.emit('clicked')
        start.assert_called_once_with()
        self.assertEqual(self.app.settings.get('source'), 'desktop')  # what the captions use, not the waiting choice
        self.assertTrue(self.window.apply_button.get_sensitive())  # the microphone still waits for Apply
        self.assertTrue(self.checks()['mic'].get_active())

    def test_a_closed_window_is_freed(self):
        self.settle()  # shown, so the sample is playing
        self.assertTrue(self.window.preview.timer)
        self.checks()['rain'].activate()  # the handlers GTK holds have all run
        self.window.apply_button.emit('clicked')
        self.window.preview.row_height = None
        self.window.preview.label.styled()  # the sample still fits itself when GTK restyles it
        self.assertTrue(self.window.preview.row_height)
        window, preview = weakref.ref(self.window), weakref.ref(self.window.preview)
        self.window.close()
        del self.window
        self.settle()
        gc.collect()
        self.assertIsNone(window())
        self.assertIsNone(preview())


if __name__ == '__main__':
    unittest.main()
