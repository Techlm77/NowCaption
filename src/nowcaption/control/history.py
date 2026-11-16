"""The kept transcript history: this session's file, the earlier sessions still kept, deleting them,
and the menu's Keep history and how many days to keep. The files themselves are engine's; uses the
app's settings, window, transcript window and recognition worker (app.listening); never imports the
windows (ui), and app.py only for annotations."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from gi.repository import GLib

from .. import engine
from ..engine import session_heading

if TYPE_CHECKING:  # for annotations only
    from ..app import NowCaption

LOG = logging.getLogger('nowcaption')


class History:
    """This session's history file (made once something is said, while history is on) and the
    sessions kept before it."""

    def __init__(self, app: NowCaption) -> None:
        self.app = app
        self.history_path = engine.history_file(app.started)  # created once something is said

    def history_days(self) -> int:
        """How many days sessions are kept, 0 for until deleted."""
        return self.app.settings.get('history_days')

    def prune_history(self) -> None:
        """Delete the sessions no longer kept (see engine.prune_history)."""
        engine.prune_history(days=self.history_days())

    def on_history(self, action, value):
        """The menu's Keep history: from now on this session is kept in its file, or no longer."""
        keep = not action.get_state().get_boolean()
        action.set_state(GLib.Variant('b', keep))
        self.app.settings.set('history', keep)
        if self.app.listening.worker:
            self.app.listening.worker.set_history(self.history_path if keep else None)
        if self.app.transcript:
            self.app.transcript.show_title()

    def on_history_days(self, action, value):
        """Keep sessions this many days (0: until deleted). A shorter time deletes the older ones now."""
        try:
            days = int(value.get_string())
        except ValueError:
            return
        if days not in engine.HISTORY_CHOICES:
            return
        action.set_state(value)
        self.app.settings.set('history_days', days)
        self.prune_history()
        if self.app.window:
            self.app.window.refresh_menu()
        if self.app.transcript:
            self.app.transcript.show_title()
            self.app.transcript.reload()

    def earlier_sessions(self) -> list[tuple[float, str]]:
        """Kept sessions before this one, oldest first, as (start time, text)."""
        sessions = []
        cutoff = engine.history_cutoff(self.history_days())
        for started, path in engine.history_files():
            if path == self.history_path:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    continue  # no longer kept, even if prune_history has not deleted it yet
                text = path.read_text(errors='replace').strip()
            except OSError as e:
                LOG.warning('Could not read the transcript %s: %s', path, e)
                continue
            if text:
                sessions.append((started, text))
        return sessions

    def full_transcript(self, current: str) -> str:
        """Every kept session and this one, each under its heading, as Copy and Save give it."""
        parts = [f'{session_heading(started)}\n{text}' for started, text in self.earlier_sessions()]
        if current.strip():
            parts.append(f'{session_heading(self.app.started)}\n{current.strip()}')
        return '\n\n'.join(parts)

    def delete_history(self) -> None:
        """Delete every kept session, this one included, and clear the captions."""
        for _, path in engine.history_files():
            try:
                path.unlink()
            except OSError as e:
                LOG.warning('Could not delete the transcript %s: %s', path, e)
        self.app.listening.clear_transcript()
        if self.app.listening.worker and self.app.settings.get('history'):
            self.app.listening.worker.set_history(self.history_path)  # what is said next starts a new file
        if self.app.transcript:
            self.app.transcript.reload()
