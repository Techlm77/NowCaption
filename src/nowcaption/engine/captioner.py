"""Caption text from 16 kHz audio: the speech model's words, split into lines at pauses and
punctuated together as one passage, plus the session's transcript.

The recognizer stream is never reset at pauses. Resetting drops the first words
of the next phrase (it doubled the word error rate in testing), so pauses only
split caption lines. Silence is fed briefly to flush words, then recognition waits.

No threads: RecognitionWorker (worker.py) owns the Captioner. Depends on words.py and
transcript.py only.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable

import numpy as np

from ..words import WordFixes
from .transcript import TranscriptStore, stamp

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


def tidy(text: str) -> str:
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

    def __init__(self, recognizer, punctuation=None, on_update: Callable[[str], None] | None = None,
                 on_warning: Callable[[str], None] | None = None, store: TranscriptStore | None = None,
                 keep_raw: bool = False, fixes: WordFixes | None = None) -> None:
        """recognizer: a sherpa-onnx OnlineRecognizer (see models.load_recognizer); punctuation: an
        OnlinePunctuation, or None for sentence case only. on_warning(message) says when something
        degrades; store keeps the finished lines (a new TranscriptStore by default); keep_raw also
        keeps the raw text, for raw_text()."""
        self.rec, self.punct = recognizer, punctuation
        self.fixes = fixes or WordFixes()  # the user's word list
        self.on_update, self.on_warning = on_update, on_warning
        # settled lines, until cleared
        self.store = store if store is not None else TranscriptStore(on_warning=on_warning)
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

    def format(self, text: str) -> str:
        """The model's words punctuated and cased, then spelled the word list's way. Without the
        punctuation model (or once it has failed), and for mixed-case text, tidy() does the casing."""
        text = text.strip()
        if self.punct and text and (text.islower() or text.isupper()):
            try:
                return self.spell(self.punct.add_punctuation_with_case(text.lower()).rstrip(',;'))
            except Exception as e:  # keep captioning without punctuation rather than stop
                LOG.exception('Punctuation failed')
                self.punct = None
                if self.on_warning:
                    self.on_warning(f'Punctuation turned off after an error: {e}')
        return self.spell(tidy(text))

    def spell(self, text: str) -> str:
        """Punctuated text with the word list's spelling."""
        return ' '.join(self.fixes.spelled(text.split())) if self.fixes else text

    def heard(self, raw: str) -> str:
        """The model's words with the word list's fixes for what it mishears."""
        return self.fixes.heard(raw) if self.fixes else raw

    def set_fixes(self, fixes: WordFixes) -> None:
        """A new word list. Its spelling applies to the lines still live and the words being
        spoken; what it says the model mishears, to words heard from now on."""
        self.fixes = fixes
        self.live_input = None  # punctuate the live passage again, now with the new list
        if self.on_update and (self.partial.strip() or self.count > self.settled):
            self.on_update(self.caption_text())

    def feed(self, pcm16: bytes | bytearray | memoryview | np.ndarray) -> None:
        """Recognise a block of 16 kHz mono PCM16 audio (bytes, or an int16 array). Digital silence
        is decoded for SILENCE_FLUSH_S; then the last words are flushed once and silence is skipped."""
        x = np.frombuffer(pcm16, dtype=np.int16) if isinstance(pcm16, (bytes, bytearray, memoryview)) else pcm16
        if not x.any():
            self.silent_for += len(x) / RATE
            if self.silent_for < SILENCE_FLUSH_S:
                return self._decode(np.zeros(len(x), np.float32))
            self.flush()  # push the model's lookahead through once, then stay idle
            return
        self.silent_for, self.flushed = 0.0, False
        self._decode(x.astype(np.float32) / 32768)

    def flush(self) -> None:
        """End the words being spoken as a line: 0.8 s of silence pushes the model's lookahead
        through. Only once until new sound arrives."""
        if not self.flushed:
            self.flushed = True
            self._decode(np.zeros(int(0.8 * RATE), np.float32), force=True)

    def clear(self) -> None:
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

    def finish(self) -> None:
        """NowCaption is quitting: the words still being spoken and the newest lines become
        final, punctuated as they were last shown, so the session's history keeps them."""
        self.commit_partial()
        self.caption_text()
        self._settle(0)

    def commit_partial(self) -> None:
        """Keep the last known visible words, even if the old native stream has failed."""
        raw = self.partial.strip()
        if raw:
            self.add_line(raw)
        self.partial, self.partial_since = '', None

    def continue_from(self, old: Captioner) -> None:
        """Carry a replaced captioner's session over: its lines and frozen punctuation."""
        old.commit_partial()
        self.store, self.punct, self.raw = old.store, old.punct, old.raw
        self.fixes = old.fixes
        self.recent, self.first, self.settled = old.recent, old.first, old.settled
        self.shown, self.live = old.shown.copy(), old.live.copy()
        self.live_input, self.live_exact = old.live_input, old.live_exact

    @property
    def count(self) -> int:
        """Finished lines since start or the last clear."""
        return self.first + len(self.recent)

    def add_line(self, raw: str) -> None:
        """A finished line, timestamped when it started rather than when it ended."""
        if self.raw is not None:
            self.raw.append(raw)
        line = (self.partial_since or time.time(), None, self.heard(raw))
        self.recent.append(line)
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

    def caption_text(self, growing: bool = False) -> str:
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
        partial = self.heard(self.partial.strip())
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

    def punctuated(self, context: Iterable[str], words: Iterable[str]) -> list[str]:
        """The words punctuated as they read after the context words."""
        context, words = list(context), list(words)
        out = self.format(' '.join(context + words)).split() if words else []
        if len(out) == len(context) + len(words):  # punctuation keeps one output word per input word
            return out[len(context):]
        return self.format(' '.join(words)).split()

    def grown(self, key: tuple[tuple[str, ...], tuple[str, ...]]) -> list[str] | None:
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
        out = live[:-1] + [word + shown[len(core):]]
        return self.fixes.spelled(out) if self.fixes else out  # it may have grown into a listed word

    def settle(self, line: tuple[float, None, str]) -> str:
        """The words of a line leaving the live passage, punctuated as they were last shown.
        A line that was never shown (nothing displays captions) is punctuated on its own."""
        n = len(line[2].split())
        if len(self.live) >= n:
            words, self.live = self.live[:n], self.live[n:]
            return ' '.join(words)
        self.live = []
        return self.format(line[2])

    def transcript(self) -> str:
        """Every finished line since start or the last clear, plus the words still being spoken."""
        self.caption_text()  # also formats the live lines when there is no on-screen consumer
        out = self.store.lines()
        pending = [(ts, raw) for ts, _, raw in self.recent[self.settled - self.first:]]
        if self.partial.strip():
            pending.append((self.partial_since or time.time(), self.heard(self.partial.strip())))
        offset = 0
        for i, (ts, raw) in enumerate(pending):
            # Preserve the entire visible passage even if a punctuation backend
            # changes whitespace: the final line receives all remaining words.
            end = len(self.live) if i == len(pending) - 1 else offset + len(raw.split())
            out.append(stamp(ts) + ' '.join(self.live[offset:end]))
            offset = end
        return '\n'.join(out)

    def raw_text(self) -> str:
        """Complete recognised text for optional benchmark tracing, without timestamps.

        Reading this does not run punctuation again. The normal caption update has
        already paid that cost before the benchmark receives its trace.
        """
        if self.raw is None:
            raise RuntimeError('raw_text() needs Captioner(keep_raw=True)')
        return ' '.join(self.raw + [self.partial.strip()]).strip()
