"""The transcript history folder: each session's file, which sessions are kept, deleting those
older than the user's choice, and the words the menu and the transcript use for them. Writing the
files is TranscriptStore's job (transcript.py). Depends on nothing else in NowCaption.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

LOG = logging.getLogger(__name__)

HISTORY_DAYS = 7  # by default, sessions older than this are deleted
HISTORY_CHOICES = (1, 7, 30, 90, 0)  # what More options offers; 0 keeps sessions until deleted


def history_span(days: int) -> str | None:
    """How long sessions are kept, as the menu and the transcript say it: "7 days", "1 day", or
    None when they are kept until deleted."""
    return None if not days else '1 day' if days == 1 else f'{days} days'


def history_title(days: int) -> str:
    """How the transcript window says what it shows, for sessions kept this long."""
    return 'Last 24 hours' if days == 1 else f'Last {days} days' if days else 'Every kept session'


def session_heading(started: float) -> str:
    """A session's heading in the transcript, such as "Thursday 23 October 2026, 14:05"."""
    return time.strftime('%A %-d %B %Y, %H:%M', time.localtime(started))


def history_dir() -> Path:
    """$XDG_DATA_HOME/nowcaption/history: one file per session, named by when it started
    (in the Flatpak, ~/.var/app/<app id>/data/nowcaption/history)."""
    base = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return Path(base) / 'nowcaption' / 'history'


def history_file(started: float, folder: str | Path | None = None) -> Path:
    """The history file of a session started at this time (seconds since the epoch)."""
    return Path(folder or history_dir()) / time.strftime('%Y-%m-%d %H.%M.%S.txt', time.localtime(started))


def history_files(folder: str | Path | None = None) -> list[tuple[float, Path]]:
    """Kept sessions, oldest first, as (start time, path)."""
    sessions = []
    for path in Path(folder or history_dir()).glob('????-??-?? ??.??.??.txt'):
        try:
            sessions.append((time.mktime(time.strptime(path.stem, '%Y-%m-%d %H.%M.%S')), path))
        except ValueError:
            continue
    return sorted(sessions)


def history_cutoff(days: int = HISTORY_DAYS, now: float | None = None) -> float:
    """Sessions that ended (were last written) before this time are no longer kept; with days 0
    (kept until deleted), none."""
    if not days:
        return float('-inf')
    return (now or time.time()) - days * 86400


def prune_history(folder: str | Path | None = None, days: int = HISTORY_DAYS, now: float | None = None) -> None:
    """Delete sessions that ended (were last written) more than `days` ago (none with days 0)."""
    if not days:
        return
    cutoff = history_cutoff(days, now)
    for _, path in history_files(folder):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError as e:
            LOG.warning('Could not delete old transcript %s: %s', path, e)
