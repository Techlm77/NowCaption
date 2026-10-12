"""Speech recognition: a sherpa-onnx streaming transducer plus a punctuation model.

The recognizer stream is never reset at pauses. Resetting drops the first words
of the next phrase (it doubled the word error rate in testing), so pauses only
split caption lines. Silence is fed briefly to flush words, then recognition waits.
"""
import logging
import os
from collections import deque
import re
import threading
import time
from pathlib import Path

import numpy as np

LOG = logging.getLogger(__name__)

RATE = 16000
SILENCE_FLUSH_S = 1.0   # digital silence before the last words are flushed
PAUSE_S = 0.8           # no new words for this long starts a new caption line
MAX_LINE = 160          # characters before a running line is split anyway
ASR_DIR = 'nowcaption-fastconformer-en-80ms-cc-by-v1'
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
    p = models_dir() / ASR_DIR
    return so.OnlineRecognizer.from_transducer(
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

    def __init__(self, recognizer, punctuation=None, on_update=None, on_warning=None):
        self.rec, self.punct = recognizer, punctuation
        self.on_update, self.on_warning = on_update, on_warning
        self.lines = []  # (time, formatted, raw) for the whole session, until cleared
        self.shown = []  # punctuation each settled line was last shown with, frozen so text never jumps
        self.live = []  # words of the live passage as last shown, for freezing its oldest line
        self.stream = recognizer.create_stream()
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
        self.lines = []
        self.shown, self.live = [], []
        self.done = 0
        self.partial, self.quiet, self.partial_since = '', 0.0, None
        self.silent_for, self.flushed = 0.0, True
        self.decoded = False
        if self.on_update:
            self.on_update('')
        self.stream = self.rec.create_stream()

    def commit_partial(self):
        """Keep the last known visible words, even if the old native stream has failed."""
        raw = self.partial.strip()
        if raw:
            self.lines.append((self.partial_since or time.time(), self.format(raw), raw))
        self.partial, self.partial_since = '', None

    def _decode(self, samples, force=False):
        rec, s = self.rec, self.stream
        s.accept_waveform(RATE, samples)
        while rec.is_ready(s):
            rec.decode_stream(s)
            self.decoded = True
        text = rec.get_result(s)
        new, committed = text[self.done:], False
        self.quiet = 0.0 if new != self.partial else self.quiet + len(samples) / RATE
        if new.strip() and (force or self.quiet >= PAUSE_S or len(new) > MAX_LINE):
            cut = len(new)
            if not force and len(new) > MAX_LINE:
                cut = new.rfind(' ', 0, MAX_LINE) + 1 or len(new)
            raw = new[:cut].strip()
            if raw:  # timestamped when the line started, not when it ended
                self.lines.append((self.partial_since or time.time(), self.format(raw), raw))
            self.done += cut
            new, committed = new[cut:], True
            if len(text) > 20000 and not new.strip():  # bound the stream's text at a natural break
                rec.reset(s)
                self.done = 0
        if committed or new != self.partial:
            if not new.strip():
                self.partial_since = None
            elif committed or not self.partial.strip():
                self.partial_since = time.time()
            self.partial = new
            if self.on_update:
                self.on_update(self.caption_text())

    def caption_text(self):
        """The last few lines and the words still coming, punctuated together as one passage,
        so that phrases split at pauses still read as sentences. Only the newest LIVE_LINES are
        re-punctuated; older lines keep the punctuation they were last shown with, otherwise a
        comma that comes and goes far back in the passage would re-wrap every row."""
        while len(self.lines) - len(self.shown) > self.LIVE_LINES:
            self.shown.append(self.settle(self.lines[len(self.shown)]))
        context = self.lines[len(self.shown) - 1][2].split() if self.shown else []
        live = [r for _, _, r in self.lines[len(self.shown):]]
        partial = self.partial.strip()
        words = ' '.join(live + ([partial] if partial else [])).split()
        out = self.format(' '.join(context + words)).split() if words else []
        if len(out) == len(context) + len(words):  # punctuation keeps one output word per input word
            out = out[len(context):]
        else:
            out = self.format(' '.join(words)).split()
        self.live = out
        shown = [s for s in self.shown[max(0, len(self.lines) - self.DISPLAY_LINES):] if s]
        return ' '.join(shown + out)

    def settle(self, line):
        """The words of a line leaving the live passage, punctuated as they were last shown."""
        n = len(line[2].split())
        if len(self.live) >= n:
            words, self.live = self.live[:n], self.live[n:]
            return ' '.join(words)
        self.live = []
        return line[1]

    def transcript(self):
        """Every finished line since start or the last clear, plus the words still being spoken."""
        out = [time.strftime('[%H:%M:%S] ', time.localtime(ts)) + t for ts, t, _ in self.lines]
        if self.partial.strip():
            out.append(time.strftime('[%H:%M:%S] ', time.localtime(self.partial_since or time.time()))
                       + self.format(self.partial))
        return '\n'.join(out)

    def raw_text(self):
        """Complete recognised text for optional benchmark tracing, without timestamps.

        Reading this does not run punctuation again. The normal caption update has
        already paid that cost before the benchmark receives its trace.
        """
        return ' '.join([raw for _, _, raw in self.lines] + [self.partial.strip()]).strip()


class RecognitionWorker(threading.Thread):
    """The only thread that touches the speech models.

    Other threads talk to it with the methods below; it reports back through
      on_event(kind, value), called on this thread (overload may also come from capture):
      ('ready', None)            models loaded
      ('caption', (gen, text))   new caption passage; gen is the clear() generation it belongs to
      ('snapshot', (fn, text))   reply to snapshot(fn)
      ('warning', message)       something degraded but captioning continues
      ('error', message)         recognition stopped; restart() or a new source tries again
      ('load-error', message)    the models could not be loaded; a new source or restart() retries
      ('overload', seconds)      audio arrived faster than it could be recognised and was dropped
      ('recognition-ok', gen)    the new stream processed audio successfully
    Audio is tagged with a source generation; blocks from an older source are ignored, so a
    capture thread that is still shutting down can never feed the new source's captions.
    Pending audio is bounded at enqueue time; commands are never dropped for lack of space.
    """

    MAX_BACKLOG = 20  # at most 2 s of pending capture audio, excluding one in-flight call

    def __init__(self, on_event, load=None, on_trace=None):
        super().__init__(name='nowcaption-recognition', daemon=True)
        self.on_event = on_event
        self.on_trace = on_trace  # optional complete-text trace, used only by benchmarks
        self.load = load or (lambda: load_models(lambda m: self.on_event('warning', m)))
        self.inbox = deque()  # ordered commands and at most MAX_BACKLOG audio blocks
        self.lock = threading.Condition()
        self.backlog = self.dropped = 0
        self.discontinuity = self.stopping = False
        self.source_gen, self.ui_gen = 0, 0
        self.captioner, self.broken = None, False
        self.confirmed = False

    # --- called from any thread -------------------------------------------------------------

    def feed(self, gen, pcm):
        report = None
        with self.lock:
            if self.stopping or gen != self.source_gen:
                return
            if self.backlog >= self.MAX_BACKLOG:
                # Preserve command ordering while removing only the oldest audio.
                for i, (kind, _) in enumerate(self.inbox):
                    if kind == 'audio':
                        del self.inbox[i]
                        self.backlog -= 1
                        break
                self.discontinuity = True
                self.dropped += 1
                if self.dropped >= self.MAX_BACKLOG:
                    report, self.dropped = self.dropped / 10, 0
            self.backlog += 1
            self.inbox.append(('audio', (gen, pcm)))
            self.lock.notify()
        if report:
            self.on_event('overload', report)  # visible even if the native worker is stalled

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

    def snapshot(self, fn):
        self._put('snapshot', fn)

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
                    return
                kind, value = self.inbox.popleft()
                discontinuity = False
                dropped = 0
                if kind == 'audio':
                    self.backlog -= 1
                    discontinuity, self.discontinuity = self.discontinuity, False
                    dropped, self.dropped = self.dropped, 0
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
                if dropped:
                    self.on_event('overload', dropped / 10)
            elif kind == 'clear':
                self._handle(kind, value)  # generations apply even when models failed to load
            elif kind == 'snapshot':
                self._handle(kind, value)
            elif loaded:
                if kind != 'source' or value == self.source_gen:
                    self._handle(kind, value)
            elif kind in ('restart', 'source'):  # loading failed earlier: try again
                loaded = self._load()

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
        rec, punct = self.models
        old = self.captioner
        new = Captioner(rec, punct, on_update=self._caption,
                        on_warning=lambda m: self.on_event('warning', m))
        if old:  # keep the session transcript when recovering from an error
            old.commit_partial()
            new.lines, new.punct = old.lines, old.punct
        self.captioner = new
        self.broken = False
        self.confirmed = False
        if old:
            self._caption(new.caption_text())

    def _handle(self, kind, value):
        if kind == 'source':
            if self.broken:
                self._safely(self._new_captioner)
            else:
                self._safely(self.captioner.flush)
                if not self.broken:
                    self._safely(self._new_captioner)  # a new sound source is an acoustic boundary
        elif kind == 'clear':
            self.ui_gen = value
            if self.captioner:
                self._safely(self.captioner.clear)
        elif kind == 'snapshot':
            text = self.captioner.transcript() if self.captioner else ''
            self.on_event('snapshot', (value, text))
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
            self.on_event('error', f'Speech recognition stopped: {e}')
            return False
        return True

    def _caption(self, text):
        self.on_event('caption', (self.ui_gen, text))
        if self.on_trace:
            self.on_trace(self.captioner.raw_text())
