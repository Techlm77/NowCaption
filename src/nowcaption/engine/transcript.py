"""The session's finished lines (TranscriptStore): an unnamed temporary file that Copy and Save
read, and the session's history file while history is on. Depends on nothing else in NowCaption.
"""
import logging
import os
import tempfile
import time
from pathlib import Path

LOG = logging.getLogger(__name__)


def stamp(ts):
    return time.strftime('[%H:%M:%S] ', time.localtime(ts))


def cache_dir():
    """$XDG_CACHE_HOME/nowcaption (in the Flatpak, ~/.var/app/<app id>/cache/nowcaption)."""
    base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return Path(base) / 'nowcaption'


class TranscriptStore:
    """The session's finished lines as Copy and Save export them, until Clear.

    They are appended to an unnamed temporary file in the cache folder instead of being
    kept in memory, so a long session does not make NowCaption grow. The file never has
    a name: other programs cannot open it, and the system deletes it when NowCaption
    exits, even after a crash. If it cannot be created or written, lines stay in memory.

    With history on (set_history), each line is also appended to the session's history file,
    which only the user can read and which is only created once something is said. If it cannot
    be written, on_warning(message) says so and the next line tries the file again.
    """

    def __init__(self, folder=None, on_warning=None):
        self.count = self.size = 0
        self.file, self.memory = None, None
        self.on_warning = on_warning
        self.history, self.history_fd = None, None
        self.history_written = None  # the file this session wrote to: Clear empties it even after history is off
        self.history_failed = False  # the last write failed, and on_warning said so
        try:
            folder = Path(folder) if folder else cache_dir()
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            # O_TMPFILE where supported. It stays open while the store lives, so no with block.
            self.file = tempfile.TemporaryFile(dir=folder, buffering=0)  # noqa: SIM115
        except OSError as e:
            LOG.warning('Keeping the transcript in memory: %s', e)
            self.memory = []

    def append(self, ts, text):
        line = stamp(ts) + text
        if self.file is not None:
            data = (line + '\n').encode()
            try:
                written = 0
                while written < len(data):
                    written += os.pwrite(self.file.fileno(), data[written:], self.size + written)
            except OSError as e:
                self._keep_in_memory(e)
            else:
                self.size += len(data)
        if self.memory is not None:
            self.memory.append(line)
        self.count += 1
        if self.history is not None:
            self._write_history((line + '\n').encode())

    def set_history(self, path):
        """Also keep finished lines in this file from now on, or stop when path is None."""
        self._close_history()
        self.history = Path(path) if path else None
        self.history_failed = False  # turned on again: say so if it still cannot be written

    def lines(self):
        if self.file is None:
            return list(self.memory)
        return self._read().splitlines()

    def clear(self):
        self.count = self.size = 0
        if self.file is not None:
            try:
                os.ftruncate(self.file.fileno(), 0)  # Clear frees the old text, not just hides it
            except OSError as e:
                self.memory = []
                self._keep_in_memory(e)
        if self.memory is not None:
            self.memory = []
        if self.history_written is not None:  # the session's history is cleared with it
            try:
                if self.history_fd is not None:
                    os.ftruncate(self.history_fd, 0)
                else:  # history was turned off, or back on but not written to since
                    os.truncate(self.history_written, 0)
            except FileNotFoundError:  # already deleted
                pass
            except OSError as e:
                LOG.warning('Could not clear the transcript history %s: %s', self.history_written, e)
                if self.on_warning:
                    self.on_warning(f'Could not clear the history: {e.strerror or e}')

    def __del__(self):
        if getattr(self, 'file', None) is not None:
            self.file.close()  # the system deletes the unnamed file with its last descriptor
        if getattr(self, 'history_fd', None) is not None:
            self._close_history()

    def _write_history(self, data):
        written = 0
        try:
            if self.history_fd is None:
                self.history.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.history_fd = os.open(self.history, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
                self.history_written = self.history
            while written < len(data):
                written += os.write(self.history_fd, data[written:])
        except OSError as e:  # keep captioning; the next line opens the file again
            LOG.warning('Could not write the transcript history %s: %s', self.history, e)
            if written:  # take the half line back off the end (appending leaves the offset there)
                try:
                    os.ftruncate(self.history_fd, os.lseek(self.history_fd, 0, os.SEEK_CUR) - written)
                except OSError:
                    pass
            self._close_history()
            if not self.history_failed and self.on_warning:  # once, not for every line while it fails
                self.on_warning(f'Could not save the history: {e.strerror or e}')
            self.history_failed = True
        else:
            self.history_failed = False

    def _close_history(self):
        if self.history_fd is not None:
            try:
                os.close(self.history_fd)
            except OSError:
                pass
        self.history_fd = None

    def _read(self):
        data, fd = b'', self.file.fileno()
        while len(data) < self.size:
            chunk = os.pread(fd, self.size - len(data), len(data))
            if not chunk:
                break
            data += chunk
        return data.decode(errors='replace')

    def _keep_in_memory(self, error):
        LOG.warning('Keeping the transcript in memory from now on: %s', error)
        if self.memory is None:
            try:
                self.memory = self._read().splitlines()
            except OSError:
                LOG.exception('Earlier transcript lines could not be read back')
                self.memory = []
        self.file.close()
        self.file = None
