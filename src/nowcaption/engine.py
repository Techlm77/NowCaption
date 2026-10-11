"""Speech recognition: a sherpa-onnx streaming transducer plus a punctuation model.

The recognizer stream is never reset at pauses. Resetting drops the first words
of the next phrase (it doubled the word error rate in testing), so pauses only
split caption lines. Digital silence is not fed to the model at all.
"""
import os
import re
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sherpa_onnx as so

RATE = 16000
SILENCE_FLUSH_S = 1.0   # digital silence before the last words are flushed
PAUSE_S = 0.8           # no new words for this long starts a new caption line
MAX_LINE = 160          # characters before a running line is split anyway
ASR_DIR = 'nowcaption-fastconformer-en-80ms-cc-by-v1'
PUNCT_DIR = 'sherpa-onnx-online-punct-en-2024-08-06'


def models_dir():
    """$NOWCAPTION_MODELS, the Flatpak/system install, or ./models in a checkout."""
    candidates = [os.environ.get('NOWCAPTION_MODELS'),
                  Path(__file__).resolve().parents[2] / 'models',
                  '/app/share/nowcaption/models', '/usr/local/share/nowcaption/models',
                  '/usr/share/nowcaption/models']
    for c in candidates:
        if c and (Path(c) / ASR_DIR / 'tokens.txt').exists():
            return Path(c)
    raise FileNotFoundError('Speech model not found. Run tools/fetch-models.sh or set NOWCAPTION_MODELS.')


def load_recognizer(threads=1):
    p = models_dir() / ASR_DIR
    return so.OnlineRecognizer.from_transducer(
        tokens=str(p / 'tokens.txt'), encoder=str(p / 'encoder.int8.onnx'),
        decoder=str(p / 'decoder.int8.onnx'), joiner=str(p / 'joiner.int8.onnx'),
        num_threads=threads, decoding_method='greedy_search')


def load_punctuation():
    """Small CNN-BiLSTM that restores punctuation and casing (about 4 ms per line)."""
    p = models_dir() / PUNCT_DIR
    if not (p / 'model.int8.onnx').exists():
        return None
    return so.OnlinePunctuation(so.OnlinePunctuationConfig(model_config=so.OnlinePunctuationModelConfig(
        cnn_bilstm=str(p / 'model.int8.onnx'), bpe_vocab=str(p / 'bpe.vocab'), num_threads=1)))


def tidy(text):
    """Sentence case for all-lower or all-upper text; mixed case is kept."""
    text = text.strip()
    if not text or (not text.isupper() and not text.islower()):
        return text
    text = re.sub(r"\bi\b", "I", text.lower())
    return text[0].upper() + text[1:]


class Captioner:
    """Feed 16 kHz mono int16 audio with feed(); on_update(lines, partial) gets the captions.

    on_update is called from the feeding thread.
    """

    def __init__(self, recognizer, punctuation=None, on_update=None, history=500):
        self.rec, self.punct, self.on_update = recognizer, punctuation, on_update
        self.lines = deque(maxlen=history)
        self.lock = threading.Lock()
        self.stream = recognizer.create_stream()
        self.done, self.partial, self.quiet = 0, '', 0.0
        self.silent_for, self.flushed = 0.0, True

    def format(self, text):
        text = text.strip()
        if self.punct and text and (text.islower() or text.isupper()):
            return self.punct.add_punctuation_with_case(text.lower()).rstrip(',;')
        return tidy(text)

    def feed(self, pcm16):
        x = np.frombuffer(pcm16, dtype=np.int16) if isinstance(pcm16, (bytes, bytearray, memoryview)) else pcm16
        if not x.any():
            self.silent_for += len(x) / RATE
            if self.silent_for < SILENCE_FLUSH_S:
                return self._decode(np.zeros(len(x), np.float32))
            if not self.flushed:  # push the model's lookahead through once, then stay idle
                self.flushed = True
                self._decode(np.zeros(int(0.8 * RATE), np.float32), force=True)
            return
        self.silent_for, self.flushed = 0.0, False
        self._decode(x.astype(np.float32) / 32768)

    def flush(self):
        if not self.flushed:
            self.flushed = True
            self._decode(np.zeros(int(0.8 * RATE), np.float32), force=True)

    def _decode(self, samples, force=False):
        rec, s = self.rec, self.stream
        s.accept_waveform(RATE, samples)
        while rec.is_ready(s):
            rec.decode_stream(s)
        text = rec.get_result(s)
        new, committed = text[self.done:], False
        self.quiet = 0.0 if new != self.partial else self.quiet + len(samples) / RATE
        if new.strip() and (force or self.quiet >= PAUSE_S or len(new) > MAX_LINE):
            cut = len(new)
            if not force and len(new) > MAX_LINE:
                cut = new.rfind(' ', 0, MAX_LINE) + 1 or len(new)
            raw = new[:cut].strip()
            if raw:
                with self.lock:
                    self.lines.append((time.time(), self.format(raw), raw))
            self.done += cut
            new, committed = new[cut:], True
            if len(text) > 20000 and not new.strip():  # bound memory at a natural break
                rec.reset(s)
                self.done = 0
        if committed or new != self.partial:
            self.partial = new
            if self.on_update:
                self.on_update(self.caption_text())

    def recent(self, n):
        with self.lock:
            return [t for _, t, _ in list(self.lines)[-n:]]

    def caption_text(self, n=4):
        """The last few lines and the words still coming, punctuated together as one passage,
        so that phrases split at pauses still read as sentences."""
        with self.lock:
            raw = [r for _, _, r in list(self.lines)[-n:]]
        partial = self.partial.strip()
        return self.format(' '.join(raw + ([partial] if partial else [])))

    def transcript(self):
        with self.lock:
            return '\n'.join(time.strftime('[%H:%M:%S] ', time.localtime(ts)) + t for ts, t, _ in self.lines)
