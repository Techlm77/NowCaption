#!/usr/bin/env python3
"""The caption box's status row: notifications never replace the captions, and a failure stays
until it is resolved. The caption window's and the application's own methods
(support/app_loader.py) with controlled timers; no GTK.

NotificationTests come from 1.0.4, StatusTests from 1.1.2.
"""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from support.app_loader import APP, app_with_fakes
from support.fakes import Timers


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.timers = Timers()
        self.patch = patch.dict(APP, GLib=self.timers)
        self.patch.start()
        self.window = APP['CaptionWindow']()
        window = self.window
        window.text, window.status, window.status_timer = '', '', None
        window.status_sticky = False
        window.failures, window.status_scope = {}, None
        window.x = window.y = 0
        window.row_height, window.status_height, window.rows = 30, 24, 2
        window.layout = None
        # The caption rows and fades are GTK's; these tests are about which text goes where.
        window.caption = NS(show=lambda text, status=False, waiting=False: setattr(window, 'visible', text or ' '))
        window.fade = lambda widget, start, end, ms, done=None: done and done()
        window.footer_shown = window.footer_hiding = False
        window.edge = None  # floating: status messages get their own row
        window.status_label = NS(
            set_text=lambda text: setattr(window, 'notice', text),
            set_tooltip_text=lambda text: setattr(window, 'tooltip', text),
            set_visible=lambda visible: setattr(window, 'notice_visible', visible))
        window.place = lambda x, y: setattr(window, 'placed_height', window.box_height())

    def tearDown(self):
        self.patch.stop()

    def test_copy_save_warnings_and_overload_never_replace_caption_text(self):
        for message, seconds in [('Transcript copied.', 3), ('Transcript saved.', 4),
                                 ('Punctuation disabled.', 6), ('Could not save.', 8),
                                 ('Falling behind.', 4)]:
            self.window.show_status(message, seconds)
            self.window.show_text('current live speech')
            self.assertEqual(self.window.visible, 'current live speech')
            self.assertEqual(self.window.notice, message)
            self.assertTrue(self.window.notice_visible)
            self.assertEqual(self.window.tooltip, message)

    def test_first_caption_immediately_replaces_timed_status_in_main_caption_rows(self):
        self.window.show_status('Audio is back.', 3)
        self.assertEqual(self.window.visible, 'Audio is back.')
        self.window.show_text('first words after recovery')
        self.assertEqual(self.window.visible, 'first words after recovery')
        self.assertTrue(self.window.notice_visible)

    def test_footer_disappears_and_input_height_shrinks_when_notification_expires(self):
        self.window.show_text('live speech')
        height = self.window.box_height()
        self.window.show_status('Transcript copied.', 3)
        self.assertEqual(self.window.placed_height, height + self.window.status_height)
        self.timers.fire(self.window.status_timer)
        self.assertFalse(self.window.notice_visible)
        self.assertEqual(self.window.placed_height, height)
        self.assertEqual(self.window.visible, 'live speech')

    def test_save_completion_status_stays_visible_while_live_captions_continue(self):
        self.window.show_status('Finishing transcript save before quitting…', sticky=True)
        self.window.show_text('speech while the destination is slow')
        self.assertEqual(self.window.visible, 'speech while the destination is slow')
        self.assertTrue(self.window.notice_visible)
        self.assertEqual(self.window.notice, 'Finishing transcript save before quitting…')

    def test_listening_status_is_cleared_by_the_first_caption(self):
        self.window.show_status('Listening…')
        self.window.show_text('first words')
        self.assertEqual(self.window.visible, 'first words')
        self.assertFalse(self.window.notice_visible)


class StatusTests(unittest.TestCase):
    def setUp(self):
        holder = NotificationTests()
        holder.setUp()
        self.addCleanup(holder.tearDown)
        self.window, self.timers = holder.window, holder.timers
        self.app = app_with_fakes()
        self.app.listening.ready = True
        self.app.window = self.window
        self.app.listening.worker = NS(broken=True, set_source=lambda gen: None)
        self.window.show_text('Already displayed speech')

    def test_buffered_captions_and_retry_start_do_not_clear_capture_failure(self):
        self.app.listening.capture_ended(0, 'device disappeared')
        message = self.window.status
        self.app.listening.on_worker('caption', (0, 'Already displayed speech plus buffered words'))
        self.assertEqual(self.window.status, message)
        self.app.listening.start_capture()
        self.assertEqual(self.window.status, message)
        self.app.listening.capture_started(self.app.listening.source_gen - 1)
        self.assertIn('capture', self.window.failures)
        self.app.listening.capture_started(self.app.listening.source_gen)
        self.assertNotIn('capture', self.window.failures)
        self.assertEqual(self.window.status, 'Audio is back.')

    def test_a_temporary_notification_restores_an_unresolved_failure(self):
        self.app.listening.capture_ended(0, 'device disappeared')
        message = self.window.status
        self.window.show_status('Transcript copied.', seconds=3)
        self.window.show_text('More buffered speech')
        self.timers.fire(self.window.status_timer)
        self.assertEqual(self.window.status, message)
        self.assertTrue(self.window.status_sticky)

    def test_recognition_success_cannot_clear_an_unrelated_capture_failure(self):
        self.app.listening.capture_ended(0, 'device disappeared')
        self.app.listening.on_worker('error', (self.app.listening.source_gen, 0, 'inference failed'))
        self.app.listening.on_worker('caption', (0, 'Replayed old words'))
        self.assertIn('inference failed', self.window.status)
        self.app.listening.on_worker('recognition-ok', 0)
        self.assertNotIn('recognition', self.window.failures)
        self.assertIn('device disappeared', self.window.status)
        self.assertIn('capture', self.window.failures)

    def test_retrying_the_model_replaces_its_load_error_until_it_loads(self):
        restarts = []
        self.app.listening.ready = False
        self.app.listening.worker = NS(restart=lambda: restarts.append(True), set_source=lambda gen: None)
        self.app.listening.on_worker('load-error', 'Could not load the speech model: missing file.')
        self.app.listening.on_source(NS(set_state=lambda value: None), NS(get_string=lambda: 'mic'))
        self.assertEqual(restarts, [True])
        self.assertEqual(self.window.status, 'Retrying the speech model…')
        self.window.show_status('Transcript copied.', seconds=3)
        self.timers.fire(self.window.status_timer)
        self.assertEqual(self.window.status, 'Retrying the speech model…')
        self.app.listening.on_worker('ready', None)
        self.assertNotIn('model', self.window.failures)
        self.assertEqual(self.window.status, '')

    def test_stale_recognition_success_does_not_clear_the_current_failure(self):
        self.app.listening.source_gen = 2
        self.app.listening.on_worker('error', (self.app.listening.source_gen, 0, 'current inference failed'))
        self.app.listening.on_worker('recognition-ok', 1)
        self.assertIn('recognition', self.window.failures)
        self.assertIn('current inference failed', self.window.status)


if __name__ == '__main__':
    unittest.main(verbosity=2)
