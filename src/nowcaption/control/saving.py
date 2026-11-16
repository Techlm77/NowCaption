"""Copying and saving the transcript (every kept session and this one, see History.full_transcript).
Save asks where, then writes in the background, each destination's writes in order, and Quit waits
for them (app.quitting). Uses the app's window and recognition worker (app.listening); never imports
the windows (ui), and app.py only for annotations."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk

if TYPE_CHECKING:  # for annotations only
    from ..app import NowCaption

LOG = logging.getLogger(__name__)


class Saving:
    """Copy and Save, and the writes they have queued."""

    def __init__(self, app: NowCaption) -> None:
        self.app = app
        self.save_queues = {}  # destination URI -> accepted writes, in snapshot order

    def copy_transcript(self) -> None:
        """Copy every kept session and this one to the clipboard (the menu's Copy)."""
        def copy(text):
            text = self.app.history.full_transcript(text)
            self.app.window.get_clipboard().set(text)
            self.app.window.show_status('Transcript copied.' if text else 'Nothing to copy yet.', seconds=3)
        if self.app.listening.worker:
            try:
                self.app.listening.worker.snapshot(copy)
            except Exception as e:
                self.app.window.show_status(f'Could not copy the transcript: {e}', seconds=8)

    def save_transcript(self) -> None:
        """Ask where to save every kept session and this one, then write them there in the
        background (Save, Ctrl+S)."""
        if not self.app.listening.worker or self.app.closing or self.app.quitting.quit_requested:
            return
        dialog = Gtk.FileDialog(title='Save transcript', initial_name=GLib.DateTime.new_now_local().format(
            'Captions %Y-%m-%d %H.%M.txt'))

        def chosen(d, result):
            try:
                f = d.save_finish(result)
            except GLib.Error as e:
                if not e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self.app.window.show_status(f'Could not save the transcript: {e.message}', seconds=8)
                return
            if self.app.closing or self.app.quitting.quit_requested:
                return
            self.app.quitting.begin_save()

            def failed_snapshot(error):
                self.app.window.show_status(f'Could not save the transcript: {error}', seconds=8)
                self.app.quitting.finish_save(False)

            try:
                self.app.listening.worker.snapshot(
                    lambda text: self.write_transcript(f, self.app.history.full_transcript(text), tracked=True),
                    on_error=failed_snapshot)
            except Exception as e:
                failed_snapshot(e)

        dialog.save(None, None, chosen)  # its own window: the caption overlay is too small to host it

    def write_transcript(self, f: Gio.File, text: str, tracked: bool = False) -> None:
        """Queue saves to each destination so an older write cannot replace newer text. tracked:
        Quit already waits for this save (Quitting.begin_save)."""
        if not tracked:
            self.app.quitting.begin_save()
        key = f.get_uri()
        queue = self.save_queues.setdefault(key, [])
        queue.append((f, text))
        if len(queue) == 1:
            self.start_save(key)

    def start_save(self, key: str) -> None:
        """Write one queued snapshot asynchronously; other destinations can proceed in parallel."""
        queue = self.save_queues[key]
        f, text = queue[0]

        def finished(success):
            queue.pop(0)
            if not queue:
                del self.save_queues[key]
            self.app.quitting.finish_save(success)
            if queue:
                self.start_save(key)

        def failed(error):
            LOG.warning('Saving the transcript failed: %s', error)
            self.app.window.show_status(f'Could not save the transcript: {error}', seconds=8)
            finished(False)

        def written(f, result):
            try:
                f.replace_contents_finish(result)
            except GLib.Error as e:
                failed(e.message)
                return
            self.app.window.show_status(f'Transcript saved to {f.get_basename()}.', seconds=4)
            finished(True)
        try:
            f.replace_contents_bytes_async(GLib.Bytes.new((text + '\n').encode()), None, False,
                                           Gio.FileCreateFlags.REPLACE_DESTINATION, None, written)
        except Exception as e:
            failed(e)
