"""Speech recognition: a sherpa-onnx streaming transducer plus a punctuation model.

The recognizer stream is never reset at pauses. Resetting drops the first words
of the next phrase (it doubled the word error rate in testing), so pauses only
split caption lines. Silence is fed briefly to flush words, then recognition waits.
"""
import ctypes
import logging
import os
from collections import deque
import re
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

LOG = logging.getLogger(__name__)

RATE = 16000
SILENCE_FLUSH_S = 1.0   # digital silence before the last words are flushed
WARMUP_S = 0.96         # silence before a new stream's first audio (12 encoder steps): NowCaption's own
                        # model shows words up to 80 ms later when speech starts on the first sample.
                        # 0.95 s would line 40 ms blocks up with the model's decodes (words 10 ms sooner)
                        # but moves all audio 10 ms against its steps: 0.1 points more errors on
                        # LibriSpeech test-other in 5 of 6 continuous runs, so 0.96 s stays
PAUSE_S = 0.8           # no new words for this long starts a new caption line
MAX_LINE = 160          # characters before a running line is split anyway
ASR_DIR = 'nowcaption-fastconformer-en-80ms-cc-by-v2'
ASR_FILES = ('tokens.txt', 'encoder.int8.onnx', 'decoder.int8.onnx', 'joiner.int8.onnx')
PUNCT_DIR = 'sherpa-onnx-online-punct-en-2024-08-06'
PUNCT_FILES = ('model.int8.onnx', 'bpe.vocab')


def complete(folder, names):
    """All named files exist in folder and are not empty (a cut-off download leaves empty or missing files)."""
    return all((folder / n).is_file() and (folder / n).stat().st_size > 0 for n in names)


def models_dir():
    """$NOWCAPTION_MODELS, the Flatpak/system install, or ./models in a checkout."""
    explicit = os.environ.get('NOWCAPTION_MODELS')
    if explicit:
        if not complete(Path(explicit) / ASR_DIR, ASR_FILES):
            raise FileNotFoundError(f'NOWCAPTION_MODELS={explicit} does not contain a complete {ASR_DIR}')
        return Path(explicit)
    for c in (Path(__file__).resolve().parents[2] / 'models', Path('/app/share/nowcaption/models'),
              Path('/usr/local/share/nowcaption/models'), Path('/usr/share/nowcaption/models')):
        if complete(c / ASR_DIR, ASR_FILES):
            return c
    raise FileNotFoundError('Speech model not found or incomplete. Run tools/fetch-models.sh '
                            'or set NOWCAPTION_MODELS.')


def load_recognizer(threads=1):
    import sherpa_onnx as so  # imported here so that tests and tools can import this module without it

    class Recognizer(so.OnlineRecognizer):
        def get_result(self, s):
            # sherpa-onnx 1.13.8's result.text leaks a copy of the whole text on every read
            # (py::str(PyUnicode_DecodeUTF8(...)) keeps an extra reference): about 800 MB per
            # hour of speech at 25 reads a second. Its C++ result text is the tokens joined,
            # and the tokens list converts without leaking.
            return ''.join(self.recognizer.get_result(s).tokens).strip()

    p = models_dir() / ASR_DIR
    return Recognizer.from_transducer(
        tokens=str(p / 'tokens.txt'), encoder=str(p / 'encoder.int8.onnx'),
        decoder=str(p / 'decoder.int8.onnx'), joiner=str(p / 'joiner.int8.onnx'),
        num_threads=threads, decoding_method='greedy_search')


def load_punctuation():
    """Small CNN-BiLSTM that restores punctuation and casing (about 4 ms per line)."""
    import sherpa_onnx as so
    p = models_dir() / PUNCT_DIR
    if not complete(p, PUNCT_FILES):
        raise FileNotFoundError(f'{PUNCT_DIR} is missing or incomplete')
    return so.OnlinePunctuation(so.OnlinePunctuationConfig(model_config=so.OnlinePunctuationModelConfig(
        cnn_bilstm=str(p / 'model.int8.onnx'), bpe_vocab=str(p / 'bpe.vocab'), num_threads=1)))


def load_models(on_warning=None):
    """The recognizer (required) and the punctuation model (optional: without it, captions are
    sentence-cased but not punctuated, and on_warning says why)."""
    rec = load_recognizer()
    try:
        punct = load_punctuation()
    except Exception as e:
        LOG.exception('Punctuation model unavailable')
        punct = None
        if on_warning:
            on_warning(f'Punctuation is off: {e}')
    return rec, punct


def duration(pcm):
    """Seconds of 16 kHz mono audio, given PCM16 bytes or an array of samples."""
    samples = len(pcm) // 2 if isinstance(pcm, (bytes, bytearray, memoryview)) else len(pcm)
    return samples / RATE


def release_free_memory():
    """Give memory the C allocator has freed back to the system (glibc's malloc_trim).

    After a long session glibc holds about 45 MB of freed memory that the models and
    GTK no longer use. Trimming once a minute keeps NowCaption about 16% smaller and
    takes well under a millisecond. Returns False where it is not available.
    """
    global _malloc_trim
    if _malloc_trim is None:
        try:
            _malloc_trim = ctypes.CDLL('libc.so.6').malloc_trim
        except (OSError, AttributeError):  # not glibc
            _malloc_trim = False
    if not _malloc_trim:
        return False
    _malloc_trim(0)
    return True


_malloc_trim = None


def stamp(ts):
    return time.strftime('[%H:%M:%S] ', time.localtime(ts))


def cache_dir():
    """$XDG_CACHE_HOME/nowcaption (in the Flatpak, ~/.var/app/<app id>/cache/nowcaption)."""
    base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return Path(base) / 'nowcaption'


HISTORY_DAYS = 7  # sessions older than this are deleted


def history_dir():
    """$XDG_DATA_HOME/nowcaption/history: one file per session, named by when it started
    (in the Flatpak, ~/.var/app/<app id>/data/nowcaption/history)."""
    base = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return Path(base) / 'nowcaption' / 'history'


def history_file(started, folder=None):
    """The history file of a session started at this time (seconds since the epoch)."""
    return Path(folder or history_dir()) / time.strftime('%Y-%m-%d %H.%M.%S.txt', time.localtime(started))


def history_files(folder=None):
    """Kept sessions, oldest first, as (start time, path)."""
    sessions = []
    for path in Path(folder or history_dir()).glob('????-??-?? ??.??.??.txt'):
        try:
            sessions.append((time.mktime(time.strptime(path.stem, '%Y-%m-%d %H.%M.%S')), path))
        except ValueError:
            continue
    return sorted(sessions)


def prune_history(folder=None, days=HISTORY_DAYS, now=None):
    """Delete sessions that ended (were last written) more than `days` ago."""
    cutoff = (now or time.time()) - days * 86400
    for _, path in history_files(folder):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError as e:
            LOG.warning('Could not delete old transcript %s: %s', path, e)


class TranscriptStore:
    """The session's finished lines as Copy and Save export them, until Clear.

    They are appended to an unnamed temporary file in the cache folder instead of being
    kept in memory, so a long session does not make NowCaption grow. The file never has
    a name: other programs cannot open it, and the system deletes it when NowCaption
    exits, even after a crash. If it cannot be created or written, lines stay in memory.

    With history on (set_history), each line is also appended to the session's history file,
    which only the user can read and which is only created once something is said.
    """

    def __init__(self, folder=None):
        self.count = self.size = 0
        self.file, self.memory = None, None
        self.history, self.history_fd = None, None
        try:
            folder = Path(folder) if folder else cache_dir()
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.file = tempfile.TemporaryFile(dir=folder, buffering=0)  # O_TMPFILE where supported
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
        if self.history_fd is not None:
            try:
                os.ftruncate(self.history_fd, 0)  # the session's history is cleared with it
            except OSError as e:
                LOG.warning('Could not clear the transcript history %s: %s', self.history, e)

    def __del__(self):
        if getattr(self, 'file', None) is not None:
            self.file.close()  # the system deletes the unnamed file with its last descriptor
        if getattr(self, 'history_fd', None) is not None:
            self._close_history()

    def _write_history(self, data):
        try:
            if self.history_fd is None:
                self.history.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.history_fd = os.open(self.history, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
            written = 0
            while written < len(data):
                written += os.write(self.history_fd, data[written:])
        except OSError as e:  # keep captioning; the history just stops here
            LOG.warning('Could not write the transcript history %s: %s', self.history, e)
            self._close_history()
            self.history = None

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


def tidy(text):
    """Sentence case for all-lower or all-upper text; mixed case is kept."""
    text = text.strip()
    if not text or (not text.isupper() and not text.islower()):
        return text
    text = re.sub(r"\bi\b", "I", text.lower())
    return text[0].upper() + text[1:]


class Captioner:
    """Turns 16 kHz mono int16 audio into caption text. Not thread-safe: one thread owns it
    (RecognitionWorker). on_update(text) receives the caption passage after each change."""

    DISPLAY_LINES = 60  # finished lines kept for the on-screen passage; the box shows its last rows
    LIVE_LINES = 2      # finished lines still re-punctuated with the words being spoken

    def __init__(self, recognizer, punctuation=None, on_update=None, on_warning=None,
                 store=None, keep_raw=False):
        self.rec, self.punct = recognizer, punctuation
        self.on_update, self.on_warning = on_update, on_warning
        self.store = store if store is not None else TranscriptStore()  # settled lines, until cleared
        # Only the lines the caption passage still needs stay in memory: recent[0] is line
        # number first, and the newest LIVE_LINES are still re-punctuated with the words
        # being spoken. The others are settled: their punctuation is frozen in shown (the
        # last DISPLAY_LINES of them), so text already on screen never jumps.
        self.recent, self.first, self.settled = [], 0, 0  # (time, None, raw): see settle()
        self.shown = []
        self.live = []  # words of the live passage as last shown, for freezing its oldest line
        self.live_input = None  # exporting unchanged words reuses their on-screen punctuation
        self.live_exact = True  # False while the last word's punctuation is copied: see caption_text()
        self.raw = [] if keep_raw else None  # the whole session's raw text, for benchmark tracing only
        self.stream = recognizer.create_stream()
        self.primed = False  # see WARMUP_S
        self.result = None  # the stream's text after its last decode
        self.done, self.partial, self.quiet = 0, '', 0.0
        self.partial_since = None  # when the words still being spoken started
        self.silent_for, self.flushed = 0.0, True
        self.decoded = False  # audio buffering alone does not prove native inference works

    def format(self, text):
        text = text.strip()
        if self.punct and text and (text.islower() or text.isupper()):
            try:
                return self.punct.add_punctuation_with_case(text.lower()).rstrip(',;')
            except Exception as e:  # keep captioning without punctuation rather than stop
                LOG.exception('Punctuation failed')
                self.punct = None
                if self.on_warning:
                    self.on_warning(f'Punctuation turned off after an error: {e}')
        return tidy(text)

    def feed(self, pcm16):
        x = np.frombuffer(pcm16, dtype=np.int16) if isinstance(pcm16, (bytes, bytearray, memoryview)) else pcm16
        if not x.any():
            self.silent_for += len(x) / RATE
            if self.silent_for < SILENCE_FLUSH_S:
                return self._decode(np.zeros(len(x), np.float32))
            self.flush()  # push the model's lookahead through once, then stay idle
            return
        self.silent_for, self.flushed = 0.0, False
        self._decode(x.astype(np.float32) / 32768)

    def flush(self):
        if not self.flushed:
            self.flushed = True
            self._decode(np.zeros(int(0.8 * RATE), np.float32), force=True)

    def clear(self):
        """Clear is an explicit acoustic boundary: buffered old speech must not return."""
        self.store.clear()
        self.recent, self.first, self.settled = [], 0, 0
        self.shown, self.live = [], []
        self.live_input, self.live_exact = None, True
        if self.raw is not None:
            self.raw = []
        self.done = 0
        self.partial, self.quiet, self.partial_since = '', 0.0, None
        self.silent_for, self.flushed = 0.0, True
        self.decoded = False
        if self.on_update:
            self.on_update('')
        self.stream = self.rec.create_stream()
        self.primed = False
        self.result = None

    def finish(self):
        """NowCaption is quitting: the words still being spoken and the newest lines become
        final, punctuated as they were last shown, so the session's history keeps them."""
        self.commit_partial()
        self.caption_text()
        self._settle(0)

    def commit_partial(self):
        """Keep the last known visible words, even if the old native stream has failed."""
        raw = self.partial.strip()
        if raw:
            self.add_line(raw)
        self.partial, self.partial_since = '', None

    def continue_from(self, old):
        """Carry a replaced captioner's session over: its lines and frozen punctuation."""
        old.commit_partial()
        self.store, self.punct, self.raw = old.store, old.punct, old.raw
        self.recent, self.first, self.settled = old.recent, old.first, old.settled
        self.shown, self.live = old.shown.copy(), old.live.copy()
        self.live_input, self.live_exact = old.live_input, old.live_exact

    @property
    def count(self):
        """Finished lines since start or the last clear."""
        return self.first + len(self.recent)

    def add_line(self, raw):
        """A finished line, timestamped when it started rather than when it ended."""
        line = (self.partial_since or time.time(), None, raw)
        self.recent.append(line)
        if self.raw is not None:
            self.raw.append(raw)
        if self.count - self.settled > self.DISPLAY_LINES + self.LIVE_LINES:
            self._settle()  # nothing is showing captions: memory stays bounded all the same

    def _settle(self, live=None):
        """Freeze the punctuation of all but the newest `live` (LIVE_LINES) lines, and forget
        the lines the passage no longer needs."""
        live = self.LIVE_LINES if live is None else live
        while self.count - self.settled > live:
            line = self.recent[self.settled - self.first]
            text = self.settle(line)
            # Copy/Save must retain the same punctuation that freezes on screen.
            # The newest LIVE_LINES remain editable in recent until this point.
            self.store.append(line[0], text)
            self.shown.append(text)
            self.settled += 1
            self.live_input = None
        del self.shown[:-self.DISPLAY_LINES]
        keep = max(self.first, self.settled - 1)  # the newest settled line is context for the live ones
        del self.recent[:keep - self.first]
        self.first = keep

    def _decode(self, samples, force=False):
        rec, s = self.rec, self.stream
        if not self.primed:  # silence in front of the stream's first block: see WARMUP_S
            self.primed = True
            s.accept_waveform(RATE, np.concatenate([np.zeros(int(WARMUP_S * RATE), np.float32), samples]))
        else:
            s.accept_waveform(RATE, samples)
        ran = False
        while rec.is_ready(s):
            rec.decode_stream(s)
            ran = self.decoded = True
        if ran or self.result is None:  # only a decode changes it: one 40 ms block in four here
            self.result = rec.get_result(s)
        text = self.result
        new, committed = text[self.done:], False
        self.quiet = 0.0 if new != self.partial else self.quiet + len(samples) / RATE
        if new.strip() and (force or self.quiet >= PAUSE_S or len(new) > MAX_LINE):
            cut = len(new)
            if not force and len(new) > MAX_LINE:
                cut = new.rfind(' ', 0, MAX_LINE) + 1 or new.find(' ') + 1
            if cut:
                raw = new[:cut].strip()
                if raw:
                    self.add_line(raw)
                self.done += cut
                new, committed = new[cut:], True
                if len(text) > 20000 and not new.strip():  # bound the stream's text at a natural break
                    rec.reset(s)
                    self.done, self.result = 0, None
        if committed or new != self.partial:
            if not new.strip():
                self.partial_since = None
            elif committed or not self.partial.strip():
                self.partial_since = time.time()
            self.partial = new
            if self.on_update:
                self.on_update(self.caption_text(growing=not committed))

    def caption_text(self, growing=False):
        """The last few lines and the words still coming, punctuated together as one passage,
        so that phrases split at pauses still read as sentences. Only the newest LIVE_LINES are
        re-punctuated; older lines keep the punctuation they were last shown with, otherwise a
        comma that comes and goes far back in the passage would re-wrap every row.

        growing: the words still being spoken changed. When only the last word grew (the model
        spells long words out a piece at a time: a quarter of all updates), the punctuation
        shown for it is reused instead of running the model again; the next new word, a
        finished line or an export punctuates the passage properly."""
        if not self.live_exact and self.count - self.settled > self.LIVE_LINES:
            self.live, self.live_exact = self.punctuated(*self.live_input), True  # lines freeze as fully punctuated
        self._settle()
        context = self.recent[self.settled - 1 - self.first][2].split() if self.settled else []
        live = [r for _, _, r in self.recent[self.settled - self.first:]]
        partial = self.partial.strip()
        words = ' '.join(live + ([partial] if partial else [])).split()
        key = (tuple(context), tuple(words))
        if key != self.live_input or not (growing or self.live_exact):
            out = self.grown(key) if growing and key != self.live_input else None
            self.live_exact = out is None
            self.live, self.live_input = self.punctuated(*key) if out is None else out, key
        out = self.live
        oldest = self.count - self.DISPLAY_LINES  # first line still in the passage
        shown = [s for s in self.shown[max(0, oldest - (self.settled - len(self.shown))):] if s]
        return ' '.join(shown + out)

    def punctuated(self, context, words):
        """The words punctuated as they read after the context words."""
        context, words = list(context), list(words)
        out = self.format(' '.join(context + words)).split() if words else []
        if len(out) == len(context) + len(words):  # punctuation keeps one output word per input word
            return out[len(context):]
        return self.format(' '.join(words)).split()

    def grown(self, key):
        """The passage as last shown, with its last word grown into key's last word and cased
        and punctuated like it ("Annie." becomes "Annie's."), or None if anything else changed."""
        old, live = self.live_input, self.live
        if not old or not key[1] or old[0] != key[0] or len(old[1]) != len(key[1]) or old[1][:-1] != key[1][:-1]:
            return None
        before, after = old[1][-1], key[1][-1]
        shown = live[-1] if len(live) == len(old[1]) else ''
        core = shown.rstrip('.,?!')
        if not after.startswith(before) or core.lower() != before:
            return None
        if core == before:
            word = after
        elif core == before[:1].upper() + before[1:] and before != 'i':  # "I" may grow into "it"
            word = after[:1].upper() + after[1:]
        else:
            return None
        return live[:-1] + [word + shown[len(core):]]

    def settle(self, line):
        """The words of a line leaving the live passage, punctuated as they were last shown.
        A line that was never shown (nothing displays captions) is punctuated on its own."""
        n = len(line[2].split())
        if len(self.live) >= n:
            words, self.live = self.live[:n], self.live[n:]
            return ' '.join(words)
        self.live = []
        return self.format(line[2])

    def transcript(self):
        """Every finished line since start or the last clear, plus the words still being spoken."""
        self.caption_text()  # also formats the live lines when there is no on-screen consumer
        out = self.store.lines()
        pending = [(ts, raw) for ts, _, raw in self.recent[self.settled - self.first:]]
        if self.partial.strip():
            pending.append((self.partial_since or time.time(), self.partial))
        offset = 0
        for i, (ts, raw) in enumerate(pending):
            # Preserve the entire visible passage even if a punctuation backend
            # changes whitespace: the final line receives all remaining words.
            end = len(self.live) if i == len(pending) - 1 else offset + len(raw.split())
            out.append(stamp(ts) + ' '.join(self.live[offset:end]))
            offset = end
        return '\n'.join(out)

    def raw_text(self):
        """Complete recognised text for optional benchmark tracing, without timestamps.

        Reading this does not run punctuation again. The normal caption update has
        already paid that cost before the benchmark receives its trace.
        """
        if self.raw is None:
            raise RuntimeError('raw_text() needs Captioner(keep_raw=True)')
        return ' '.join(self.raw + [self.partial.strip()]).strip()


class RecognitionWorker(threading.Thread):
    """The only thread that touches the speech models.

    Other threads talk to it with the methods below; it reports back through
      on_event(kind, value), called on this thread (overload comes from the thread feeding audio):
      ('ready', None)            models loaded
      ('caption', (gen, text))   new caption passage; gen is the clear() generation it belongs to
      ('snapshot', (fn, text))   reply to snapshot(fn)
      ('snapshot-error', (fn, message)) reply to snapshot(..., on_error=fn) when export fails
      ('warning', message)       something degraded but captioning continues
      ('error', (source_gen, stream_gen, message)) recognition stopped; tagged to reject old errors
      ('load-error', message)    the models could not be loaded; a new source or restart() retries
      ('overload', seconds)      audio arrived faster than it could be recognised and was dropped
      ('recognition-ok', gen)    the new stream processed audio successfully
    Audio is tagged with a source generation; blocks from an older source are ignored, so a
    capture thread that is still shutting down can never feed the new source's captions.
    Pending audio is bounded at enqueue time; commands are never dropped for lack of space.
    """

    MAX_BACKLOG = 50  # at most 2 s of pending 40 ms capture blocks, excluding one in-flight call

    def __init__(self, on_event, load=None, on_trace=None):
        super().__init__(name='nowcaption-recognition', daemon=True)
        self.on_event = on_event
        self.on_trace = on_trace  # optional complete-text trace, used only by benchmarks
        self.load = load or (lambda: load_models(lambda m: self.on_event('warning', m)))
        self.inbox = deque()  # ordered commands and at most MAX_BACKLOG audio blocks
        self.lock = threading.Condition()
        self.backlog = 0
        self.discontinuity = self.stopping = False
        self.source_gen, self.ui_gen = 0, 0
        self.stream_gen = 0
        self._event_source_gen = 0  # the command/audio being processed, not a newer queued source
        self.captioner, self.broken = None, False
        self.history = None  # the session's history file, or None while history is off
        self.confirmed = False

    # --- called from any thread -------------------------------------------------------------

    def feed(self, gen, pcm):
        dropped = 0
        with self.lock:
            if self.stopping or gen != self.source_gen:
                return
            if self.backlog >= self.MAX_BACKLOG:
                # Recognition is slower than real time. Skip all pending audio at once (commands
                # keep their order) and go on from live audio after this one gap. Dropping one
                # block at a time kept captions 2 s late and restarted the stream at every block.
                dropped = sum(duration(value[1]) for kind, value in self.inbox if kind == 'audio')
                self._discard_audio()
                self.discontinuity = True
            self.backlog += 1
            self.inbox.append(('audio', (gen, pcm)))
            self.lock.notify()
        if dropped:
            self.on_event('overload', dropped)  # visible even if the native worker is stalled

    def _discard_audio(self):
        """Called with lock held; preserve user commands and discard buffered audio."""
        self.inbox = deque(item for item in self.inbox if item[0] != 'audio')
        self.backlog = 0
        self.discontinuity = False

    def _put(self, kind, value=None):
        with self.lock:
            if not self.stopping:
                self.inbox.append((kind, value))
                self.lock.notify()

    def set_source(self, gen):
        """A new capture with this generation is starting; finish the old words first."""
        with self.lock:
            if self.stopping:
                return
            self.source_gen = gen
            self._discard_audio()
            self.inbox.append(('source', gen))
            self.lock.notify()

    def clear(self, ui_gen):
        with self.lock:
            if self.stopping:
                return
            self._discard_audio()
            self.inbox.append(('clear', ui_gen))
            self.lock.notify()

    def snapshot(self, fn, on_error=None):
        with self.lock:
            if self.stopping or not self.is_alive():
                raise RuntimeError('The recognition worker is not running')
            self.inbox.append(('snapshot', (fn, on_error)))
            self.lock.notify()

    def set_history(self, path):
        """Keep finished lines in this file as well (the session's history), or stop if None."""
        self._put('history', path)

    def restart(self, recover_only=False, source_gen=None):
        """Manual restart, or a guarded automatic retry for a failed source generation."""
        self._put('recover' if recover_only else 'restart', source_gen)

    def stop(self):
        with self.lock:
            self.stopping = True
            self.inbox.clear()
            self.backlog = 0
            self.lock.notify()

    # --- the worker thread ------------------------------------------------------------------

    def run(self):
        loaded = self._load()
        while True:
            with self.lock:
                while not self.inbox and not self.stopping:
                    self.lock.wait()
                if self.stopping:
                    break
                kind, value = self.inbox.popleft()
                self._event_source_gen = (value[0] if kind == 'audio' else
                                          value if kind == 'source' else self.source_gen)
                discontinuity = False
                if kind == 'audio':
                    self.backlog -= 1
                    discontinuity, self.discontinuity = self.discontinuity, False
            if kind == 'audio':
                gen, pcm = value
                if loaded and gen == self.source_gen and not self.broken:
                    if discontinuity:
                        self._safely(self._new_captioner)
                    if self.broken:
                        continue
                    if self._safely(self.captioner.feed, pcm) and self.captioner.decoded and not self.confirmed:
                        self.confirmed = True
                        self.on_event('recognition-ok', gen)
            elif kind == 'clear':
                self._safely(self._handle, kind, value)  # generations apply even when loading failed
            elif kind in ('snapshot', 'history'):
                self._safely(self._handle, kind, value)
            elif loaded:
                if kind != 'source' or value == self.source_gen:
                    self._safely(self._handle, kind, value)
            elif kind in ('restart', 'source'):  # loading failed earlier: try again
                loaded = self._load()
        if loaded and self.captioner and not self.broken:
            self._safely(self.captioner.finish)  # the last words reach the session's history

    def _load(self):
        try:
            rec, punct = self.load()
            self.models = (rec, punct)
            self._new_captioner()
        except Exception as e:
            LOG.exception('Could not load the speech model')
            self.on_event('load-error', f'Could not load the speech model: {e}.')
            return False
        self.on_event('ready', None)
        return True

    def _new_captioner(self):
        self.stream_gen += 1
        rec, punct = self.models
        old = self.captioner
        new = Captioner(rec, punct, on_update=self._caption,
                        on_warning=lambda m: self.on_event('warning', m),
                        store=old.store if old else None, keep_raw=self.on_trace is not None)
        if old:  # keep the session transcript when recovering from an error
            new.continue_from(old)
        else:
            new.store.set_history(self.history)
        self.captioner = new
        self.broken = False
        self.confirmed = False
        if old:
            self._caption(new.caption_text())

    def _handle(self, kind, value):
        if kind == 'history':
            self.history = value
            if self.captioner:
                self.captioner.store.set_history(value)
        elif kind == 'source':
            if self.broken:
                self._safely(self._new_captioner)
            else:
                self._safely(self.captioner.flush)
                if not self.broken:
                    self._safely(self._new_captioner)  # a new sound source is an acoustic boundary
        elif kind == 'clear':
            self.ui_gen = value
            if self.captioner:
                self.stream_gen += 1
                self._safely(self.captioner.clear)
        elif kind == 'snapshot':
            callback, on_error = value
            try:
                text = self.captioner.transcript() if self.captioner else ''
            except Exception as e:
                LOG.exception('Could not prepare the transcript')
                self.on_event('snapshot-error', (on_error, str(e)))
            else:
                self.on_event('snapshot', (callback, text))
        elif kind == 'restart':
            self._safely(self._new_captioner)
        elif kind == 'recover' and self.broken and (value is None or value == self.source_gen):
            # An automatic timer may already have queued its command when a user
            # recovers manually. Never replace a healthy or newer stream for it.
            self._safely(self._new_captioner)

    def _safely(self, fn, *args):
        try:
            fn(*args)
        except Exception as e:
            LOG.exception('Speech recognition failed')
            self.broken = True
            self.on_event('error', (self._event_source_gen, self.stream_gen,
                                    f'Speech recognition stopped: {e}'))
            return False
        return True

    def _caption(self, text):
        self.on_event('caption', (self.ui_gen, text))
        if self.on_trace:
            self.on_trace(self.captioner.raw_text())
