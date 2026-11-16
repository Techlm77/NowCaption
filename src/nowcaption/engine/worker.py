"""RecognitionWorker: the one thread that touches the speech models. Other threads send it
audio and commands; it answers through on_event, on its own thread (the app hands events on to
GTK). Depends on captioner.py, models.py and words.py.
"""
import logging
import threading
from collections import deque

from ..words import WordFixes
from .captioner import RATE, Captioner
from .models import load_models

LOG = logging.getLogger(__name__)


def duration(pcm):
    """Seconds of 16 kHz mono audio, given PCM16 bytes or an array of samples."""
    samples = len(pcm) // 2 if isinstance(pcm, (bytes, bytearray, memoryview)) else len(pcm)
    return samples / RATE


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
        self.fixes = WordFixes()  # the user's word list
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

    def set_words(self, fixes):
        """Use this word list (a WordFixes) from now on."""
        self._put('words', fixes)

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
            elif kind in ('snapshot', 'history', 'words'):
                self._safely(self._handle, kind, value)
            elif loaded:
                if kind != 'source' or value == self.source_gen:
                    self._safely(self._handle, kind, value)
            elif kind in ('restart', 'source'):  # loading failed earlier: try again
                loaded = self._load()
        if loaded and self.captioner:  # finish() needs no native stream, so a failed one does not matter
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
                        store=old.store if old else None, keep_raw=self.on_trace is not None,
                        fixes=self.fixes)
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
        if kind == 'words':
            self.fixes = value
            if self.captioner:
                self.captioner.set_fixes(value)
        elif kind == 'history':
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
