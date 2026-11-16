#!/usr/bin/env python3
"""Saving the transcript: Quit waits for accepted saves, a failed save cancels the quit, and saves
to one file finish in order. The application's own methods (support/app_loader.py) with
asynchronous fake files; no GTK.

SaveTests come from 1.0.4, FailedExportTests from 1.1.2, OrderedSaveTests from 1.1.5.
"""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support import fakes as fixtures
from support.app_loader import APP, app_with_fakes
from support.fakes import Timers


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.app = app_with_fakes()
        self.app.listening.ready = True
        self.app.holds = self.app.quits = 0
        self.app.hold = lambda: setattr(self.app, 'holds', self.app.holds + 1)
        self.app.release = lambda: setattr(self.app, 'holds', self.app.holds - 1)
        self.app.quitting.quit = lambda: setattr(self.app, 'quits', self.app.quits + 1)
        self.glib = NS(
            Error=type('FileError', (Exception,), {'message': 'disk full'}),
            Bytes=NS(new=lambda content: content),
            source_remove=lambda timer: None,
            DateTime=NS(new_now_local=lambda: NS(format=lambda pattern: 'captions.txt')))
        self.patch = patch.dict(APP, GLib=self.glib, Gio=NS(FileCreateFlags=NS(REPLACE_DESTINATION=1)))
        self.patch.start()

    def tearDown(self):
        self.assertEqual(self.app.holds, 0, 'an accepted save leaked an application hold')
        self.patch.stop()

    def delayed_file(self, fail=False, immediate_error=False):
        file = NS(contents=None, callback=None)
        def write(contents, etag, backup, flags, cancellable, callback):
            if immediate_error:
                raise OSError('destination cannot start writing')
            file.contents, file.callback = contents, callback
        def finish(result):
            if fail:
                raise self.glib.Error('disk full')
        file.replace_contents_bytes_async, file.replace_contents_finish = write, finish
        file.get_basename = lambda: 'captions.txt'
        file.get_uri = lambda: f'file:///test/{id(file)}/captions.txt'
        return file

    def accept_destination(self, file):
        dialog = NS(save_finish=lambda result: file)
        dialog.save = lambda parent, cancellable, chosen: chosen(dialog, None)
        with patch.dict(APP, Gtk=NS(FileDialog=lambda **kwargs: dialog)):
            self.app.saving.save_transcript()

    def test_quit_waits_for_queued_snapshot_and_delayed_write(self):
        file = self.delayed_file()
        callbacks = []
        self.app.listening.worker = NS(snapshot=lambda callback, on_error=None: callbacks.append(callback))
        self.accept_destination(file)
        self.assertEqual(self.app.quitting.pending_saves, 1)
        self.assertEqual(self.app.holds, 1)
        self.app.quitting.request_quit()
        self.assertEqual(self.app.quits, 0)
        self.app.listening.on_worker('snapshot', (callbacks.pop(), 'requested transcript'))
        # Saved transcripts start with the session's heading (fixed in the test fakes) since 1.1.9.
        self.assertEqual(file.contents, b'Thursday 23 October 2026, 14:05\nrequested transcript\n')
        self.assertEqual(self.app.quits, 0)
        file.callback(file, None)
        self.assertEqual(self.app.quits, 1)
        self.assertEqual(self.app.quitting.pending_saves, 0)

    def test_close_button_uses_the_same_save_completion_rule(self):
        file = self.delayed_file()
        self.app.saving.write_transcript(file, 'close requested')
        self.assertTrue(self.app.quitting.on_window_close(None))
        self.assertEqual(self.app.quits, 0)
        file.callback(file, None)
        self.assertEqual(self.app.quits, 1)

    def test_multiple_accepted_saves_finish_before_quitting(self):
        first, second = self.delayed_file(), self.delayed_file()
        self.app.saving.write_transcript(first, 'first')
        self.app.saving.write_transcript(second, 'second')
        self.app.quitting.request_quit()
        first.callback(first, None)
        self.assertEqual(self.app.quits, 0)
        self.assertEqual(self.app.quitting.pending_saves, 1)
        second.callback(second, None)
        self.assertEqual(self.app.quits, 1)

    def test_failed_save_cancels_deferred_quit_and_keeps_error_visible(self):
        file = self.delayed_file(fail=True)
        self.app.saving.write_transcript(file, 'must not disappear')
        self.app.quitting.request_quit()
        file.callback(file, None)
        self.assertEqual(self.app.quits, 0)
        self.assertFalse(self.app.quitting.quit_requested)
        self.assertEqual(self.app.quitting.pending_saves, 0)
        self.assertIn('disk full', self.app.window.statuses[-1])
        self.app.quitting.request_quit()
        self.assertEqual(self.app.quits, 1)

    def test_capture_retries_continue_while_quit_waits_for_a_save(self):
        timers = Timers()
        file = self.delayed_file(fail=True)
        self.app.listening.worker = NS(set_source=lambda gen: None)
        self.app.saving.write_transcript(file, 'words')
        self.app.quitting.request_quit()
        with patch.dict(APP, GLib=timers):
            self.app.listening.capture_ended(self.app.listening.source_gen, 'device went away')
            self.assertIsNotNone(self.app.listening.retry_id)
            timers.fire(self.app.listening.retry_id)
        self.assertEqual(len(self.app.listening.capture.starts), 1)
        file.callback(file, None)
        self.assertFalse(self.app.quitting.quit_requested)

    def test_write_setup_failure_releases_its_hold(self):
        self.app.saving.write_transcript(self.delayed_file(immediate_error=True), 'words')
        self.assertEqual(self.app.quitting.pending_saves, 0)
        self.assertIn('cannot start writing', self.app.window.statuses[-1])

    def test_snapshot_request_failure_releases_its_hold(self):
        def fail(callback, on_error=None):
            raise RuntimeError('snapshot failed')
        self.app.listening.worker = NS(snapshot=fail)
        self.accept_destination(self.delayed_file())
        self.assertEqual(self.app.quitting.pending_saves, 0)
        self.assertIn('snapshot failed', self.app.window.statuses[-1])

    def test_quit_without_pending_saves_is_immediate(self):
        self.app.quitting.request_quit()
        self.assertEqual(self.app.quits, 1)


class FailedExportTests(unittest.TestCase):
    def test_export_failure_releases_an_accepted_save_and_cancels_pending_quit(self):
        holder = SaveTests()
        holder.setUp()
        self.addCleanup(holder.tearDown)
        app = holder.app
        events = fixtures.Events()
        worker = engine.RecognitionWorker(events, load=lambda: (fixtures.FakeRecognizer(), None))
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        app.listening.worker = worker
        file = holder.delayed_file()
        with patch.object(worker.captioner, 'transcript', side_effect=RuntimeError('export failed')):
            holder.accept_destination(file)
            app.quitting.request_quit()
            self.assertEqual(app.quitting.pending_saves, 1)
            app.listening.on_worker('snapshot-error', events.wait('snapshot-error'))
        self.assertEqual(app.quitting.pending_saves, 0)
        self.assertEqual(app.holds, 0)
        self.assertFalse(app.quitting.quit_requested)
        self.assertEqual(app.quits, 0)
        self.assertIsNone(file.contents)
        self.assertIn('export failed', app.window.statuses[-1])


class OrderedSaveTests(unittest.TestCase):
    def setUp(self):
        self.holder = SaveTests()
        self.holder.setUp()
        self.addCleanup(self.holder.tearDown)
        self.app = self.holder.app
        self.operations, self.disk = [], {}

    def file(self, uri, immediate_error=False):
        file = NS(get_uri=lambda: uri, get_basename=lambda: 'captions.txt')

        def write(contents, etag, backup, flags, cancellable, callback):
            if immediate_error:
                raise OSError('destination unavailable')
            self.operations.append((file, contents, callback))

        def finish(result):
            if result:
                raise self.holder.glib.Error('disk full')

        file.replace_contents_bytes_async, file.replace_contents_finish = write, finish
        return file

    def complete(self, index, fail=False):
        file, contents, callback = self.operations[index]
        if not fail:
            self.disk[file.get_uri()] = contents
        callback(file, fail)

    def test_same_destination_is_serialized_even_for_distinct_file_objects(self):
        uri = 'file:///captions.txt'
        self.app.saving.write_transcript(self.file(uri), 'earlier')
        self.app.saving.write_transcript(self.file(uri), 'earlier plus newer words')
        self.app.quitting.request_quit()
        self.assertEqual(len(self.operations), 1)
        self.assertEqual(self.app.quitting.pending_saves, 2)
        self.complete(0)
        self.assertEqual(len(self.operations), 2)
        self.assertEqual(self.app.quits, 0)
        self.complete(1)
        self.assertEqual(self.disk[uri], b'earlier plus newer words\n')
        self.assertEqual(self.app.quits, 1)
        self.assertEqual(self.app.saving.save_queues, {})

    def test_different_destinations_can_finish_in_either_order(self):
        first, second = 'file:///first.txt', 'file:///second.txt'
        self.app.saving.write_transcript(self.file(first), 'first')
        self.app.saving.write_transcript(self.file(second), 'second')
        self.assertEqual(len(self.operations), 2)
        self.complete(1)
        self.complete(0)
        self.assertEqual(self.disk, {first: b'first\n', second: b'second\n'})
        self.assertEqual(self.app.quitting.pending_saves, 0)

    def test_a_failed_write_releases_its_hold_and_the_next_snapshot_can_still_save(self):
        uri = 'file:///captions.txt'
        self.app.saving.write_transcript(self.file(uri), 'earlier')
        self.app.saving.write_transcript(self.file(uri), 'newer')
        self.app.quitting.request_quit()
        self.complete(0, fail=True)
        self.assertFalse(self.app.quitting.quit_requested)
        self.assertEqual(len(self.operations), 2)
        self.complete(1)
        self.assertEqual(self.disk[uri], b'newer\n')
        self.assertEqual(self.app.quits, 0)
        self.assertEqual(self.app.saving.save_queues, {})

    def test_failure_to_start_a_queued_write_does_not_leave_a_pending_save(self):
        uri = 'file:///captions.txt'
        self.app.saving.write_transcript(self.file(uri), 'earlier')
        self.app.saving.write_transcript(self.file(uri, immediate_error=True), 'newer')
        self.complete(0)
        self.assertEqual(self.app.quitting.pending_saves, 0)
        self.assertEqual(self.app.saving.save_queues, {})
        self.assertIn('destination unavailable', self.app.window.statuses[-1])

    def test_accepted_snapshot_requests_keep_their_order_and_holds(self):
        callbacks = []
        self.app.listening.worker = NS(snapshot=lambda callback, on_error=None: callbacks.append(callback))
        uri = 'file:///captions.txt'
        self.holder.accept_destination(self.file(uri))
        self.holder.accept_destination(self.file(uri))
        self.assertEqual(self.app.quitting.pending_saves, 2)
        for callback, text in zip(callbacks, ('earlier', 'newer')):
            self.app.listening.on_worker('snapshot', (callback, text))
        self.assertEqual(len(self.operations), 1)
        self.complete(0)
        self.complete(1)
        self.assertEqual(self.disk[uri], b'Thursday 23 October 2026, 14:05\nnewer\n')  # with its heading since 1.1.9
        self.assertEqual(self.app.quitting.pending_saves, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
