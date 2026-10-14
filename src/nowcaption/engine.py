"""Speech recognition: a sherpa-onnx streaming transducer plus a punctuation model, and
optional translation to English (Nemotron 3.5 for 33 languages, then M2M100).

The recognizer stream is never reset at pauses. Resetting drops the first words
of the next phrase (it doubled the word error rate in testing), so pauses only
split caption lines. Silence is fed briefly to flush words, then recognition waits.
"""
import ctypes
import gc
import importlib.util
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
PAUSE_S = 0.8           # no new words for this long starts a new caption line
MAX_LINE = 160          # characters before a running line is split anyway
ASR_DIR = 'nowcaption-fastconformer-en-80ms-cc-by-v1'
ASR_FILES = ('tokens.txt', 'encoder.int8.onnx', 'decoder.int8.onnx', 'joiner.int8.onnx')
PUNCT_DIR = 'sherpa-onnx-online-punct-en-2024-08-06'
PUNCT_FILES = ('model.int8.onnx', 'bpe.vocab')
MUSIC_FILTER_DIR = 'dpdfnet2'  # streaming speech enhancement (Apache-2.0), used by the optional music filter
MUSIC_FILTER_FILE = 'dpdfnet2.onnx'
# Optional translation to English: a multilingual streaming recognizer (OpenMDW-1.1) and a
# translation model (M2M100 418M, MIT, converted to CTranslate2 int8).
TRANSLATE_ASR_DIR = 'sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-320ms-int8-2026-06-11'
MT_DIR = 'nowcaption-m2m100-418m-ct2-int8-v1'
MT_FILES = ('model.bin', 'config.json', 'shared_vocabulary.json', 'sentencepiece.bpe.model')
# The languages Nemotron 3.5 recognises, by the M2M100 code they are translated from.
LANGUAGES = {
    'ar': 'Arabic', 'bg': 'Bulgarian', 'cs': 'Czech', 'da': 'Danish', 'de': 'German', 'el': 'Greek',
    'en': 'English', 'es': 'Spanish', 'et': 'Estonian', 'fi': 'Finnish', 'fr': 'French', 'he': 'Hebrew',
    'hi': 'Hindi', 'hr': 'Croatian', 'hu': 'Hungarian', 'it': 'Italian', 'ja': 'Japanese', 'ko': 'Korean',
    'lt': 'Lithuanian', 'lv': 'Latvian', 'nl': 'Dutch', 'no': 'Norwegian', 'pl': 'Polish',
    'pt': 'Portuguese', 'ro': 'Romanian', 'ru': 'Russian', 'sk': 'Slovak', 'sl': 'Slovenian',
    'sv': 'Swedish', 'th': 'Thai', 'tr': 'Turkish', 'uk': 'Ukrainian', 'vi': 'Vietnamese', 'zh': 'Chinese',
}
LANGID_CODES = {**{code: code for code in LANGUAGES}, 'nb': 'no', 'nn': 'no'}  # langid.py code -> M2M100


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


def _recognizer_class():
    import sherpa_onnx as so  # imported here so that tests and tools can import this module without it

    class Recognizer(so.OnlineRecognizer):
        def get_result(self, s):
            # sherpa-onnx 1.13.8's result.text leaks a copy of the whole text on every read
            # (py::str(PyUnicode_DecodeUTF8(...)) keeps an extra reference): about 800 MB per
            # hour of speech at 25 reads a second. Its C++ result text is the tokens joined,
            # and the tokens list converts without leaking.
            return ''.join(self.recognizer.get_result(s).tokens).strip()

    return Recognizer


def _from_transducer(cls, p, threads):
    return cls.from_transducer(
        tokens=str(p / 'tokens.txt'), encoder=str(p / 'encoder.int8.onnx'),
        decoder=str(p / 'decoder.int8.onnx'), joiner=str(p / 'joiner.int8.onnx'),
        num_threads=threads, decoding_method='greedy_search')


def load_recognizer(threads=1):
    return _from_transducer(_recognizer_class(), models_dir() / ASR_DIR, threads)


def translation_missing():
    """None when translation to English can be used, otherwise what is missing."""
    try:
        d = models_dir()
    except FileNotFoundError as e:
        return str(e)
    if not complete(d / TRANSLATE_ASR_DIR, ASR_FILES) or not complete(d / MT_DIR, MT_FILES):
        return 'The translation models are not installed. Run tools/fetch-models.sh --translation.'
    for module in ('ctranslate2', 'sentencepiece', 'langid'):
        if importlib.util.find_spec(module) is None:
            return f'Translation needs the Python package {module} (pip install {module}).'
    return None


def load_translation():
    """The multilingual recognizer and the Translator. About 1.3 GB in memory, so the app
    loads them only while translation is on."""
    class Multilingual(_recognizer_class()):
        def create_stream(self, *args, **kwargs):
            s = super().create_stream(*args, **kwargs)
            s.set_option('language', 'auto')  # Nemotron identifies the spoken language itself
            return s

    import ctranslate2
    import sentencepiece
    from langid.langid import LanguageIdentifier, model as langid_model
    d = models_dir()
    rec = _from_transducer(Multilingual, d / TRANSLATE_ASR_DIR, 1)
    mt = d / MT_DIR
    identifier = LanguageIdentifier.from_modelstring(langid_model)
    identifier.set_languages(sorted(LANGID_CODES))
    return rec, Translator(ctranslate2.Translator(str(mt), device='cpu', intra_threads=2, compute_type='int8'),
                           sentencepiece.SentencePieceProcessor(model_file=str(mt / 'sentencepiece.bpe.model')),
                           lambda text: LANGID_CODES[identifier.classify(text)[0]])


def load_punctuation():
    """Small CNN-BiLSTM that restores punctuation and casing (about 4 ms per line)."""
    import sherpa_onnx as so
    p = models_dir() / PUNCT_DIR
    if not complete(p, PUNCT_FILES):
        raise FileNotFoundError(f'{PUNCT_DIR} is missing or incomplete')
    return so.OnlinePunctuation(so.OnlinePunctuationConfig(model_config=so.OnlinePunctuationModelConfig(
        cnn_bilstm=str(p / 'model.int8.onnx'), bpe_vocab=str(p / 'bpe.vocab'), num_threads=1)))


def load_music_filter():
    """DPDFNet2, a causal speech enhancer that keeps voices and suppresses music and noise.
    It adds about 20 ms of delay and roughly 0.1 core, so the app only loads it on request."""
    import sherpa_onnx as so
    p = models_dir() / MUSIC_FILTER_DIR
    if not complete(p, (MUSIC_FILTER_FILE,)):
        raise FileNotFoundError(f'{MUSIC_FILTER_DIR}/{MUSIC_FILTER_FILE} is missing')
    config = so.OfflineSpeechDenoiserModelConfig(num_threads=1)
    config.dpdfnet.model = str(p / MUSIC_FILTER_FILE)
    return so.OnlineSpeechDenoiser(so.OnlineSpeechDenoiserConfig(model=config))


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


class TranscriptStore:
    """The session's finished lines as Copy and Save export them, until Clear.

    They are appended to an unnamed temporary file in the cache folder instead of being
    kept in memory, so a long session does not make NowCaption grow. The file never has
    a name: other programs cannot open it, and the system deletes it when NowCaption
    exits, even after a crash. If it cannot be created or written, lines stay in memory.
    """

    def __init__(self, folder=None):
        self.count = self.size = 0
        self.file, self.memory = None, None
        self.lock = threading.RLock()  # the translation thread appends from its own thread
        try:
            folder = Path(folder) if folder else cache_dir()
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.file = tempfile.TemporaryFile(dir=folder, buffering=0)  # O_TMPFILE where supported
        except OSError as e:
            LOG.warning('Keeping the transcript in memory: %s', e)
            self.memory = []

    def append(self, ts, text):
        with self.lock:
            self._append(stamp(ts) + text)

    def _append(self, line):
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

    def lines(self):
        with self.lock:
            if self.file is None:
                return list(self.memory)
            return self._read().splitlines()

    def clear(self):
        with self.lock:
            self._clear()

    def _clear(self):
        self.count = self.size = 0
        if self.file is not None:
            try:
                os.ftruncate(self.file.fileno(), 0)  # Clear frees the old text, not just hides it
            except OSError as e:
                self.memory = []
                self._keep_in_memory(e)
        if self.memory is not None:
            self.memory = []

    def __del__(self):
        if getattr(self, 'file', None) is not None:
            self.file.close()  # the system deletes the unnamed file with its last descriptor

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

    def __init__(self, recognizer, punctuation=None, on_update=None, on_warning=None, on_filter_error=None,
                 store=None, keep_raw=False):
        self.rec, self.punct = recognizer, punctuation
        self.on_update, self.on_warning = on_update, on_warning
        self.on_filter_error = on_filter_error
        self.store = store if store is not None else TranscriptStore()  # every finished line, until cleared
        # Only the lines the caption passage still needs stay in memory: recent[0] is line
        # number first, and the newest LIVE_LINES are still re-punctuated with the words
        # being spoken. The others are settled: their punctuation is frozen in shown (the
        # last DISPLAY_LINES of them), so text already on screen never jumps.
        self.recent, self.first, self.settled = [], 0, 0  # (time, formatted, raw)
        self.shown = []
        self.live = []  # words of the live passage as last shown, for freezing its oldest line
        self.raw = [] if keep_raw else None  # the whole session's raw text, for benchmark tracing only
        self.stream = recognizer.create_stream()
        self.result = None  # the stream's text after its last decode
        self.done, self.partial, self.quiet = 0, '', 0.0
        self.partial_since = None  # when the words still being spoken started
        self.silent_for, self.flushed = 0.0, True
        self.decoded = False  # audio buffering alone does not prove native inference works
        self.music_filter = None  # optional OnlineSpeechDenoiser in front of the recognizer
        self.filtering = False  # the filter holds the last few ms of the current speech

    def set_music_filter(self, music_filter):
        if music_filter is not self.music_filter:
            self._flush_filter()  # an orderly disable must keep audio already accepted by the filter
            self.music_filter, self.filtering = music_filter, False
            self._reset_filter()

    def _filter_failed(self, error):
        """An optional enhancer must never take ordinary recognition down with it."""
        LOG.exception('Music filter failed; continuing without it')
        self.music_filter, self.filtering = None, False
        if self.on_filter_error:
            self.on_filter_error(error)
        elif self.on_warning:
            self.on_warning(f'The music filter was turned off after an error: {error}')

    def _reset_filter(self):
        if self.music_filter is not None:
            try:
                self.music_filter.reset()
            except Exception as e:
                self._filter_failed(e)

    def _flush_filter(self):
        if not self.filtering:
            return
        self.filtering = False
        try:
            # The denoiser's flush() returns its tail and resets its native state.
            tail = np.asarray(self.music_filter.flush().samples, dtype=np.float32)
        except Exception as e:
            self._filter_failed(e)
            return
        if len(tail):
            self._decode(tail)  # recognizer failures still use the recognizer's recovery policy

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
            self._flush_filter()
            self.silent_for += len(x) / RATE
            if self.silent_for < SILENCE_FLUSH_S:
                return self._decode(np.zeros(len(x), np.float32))
            self.flush()  # push the model's lookahead through once, then stay idle
            return
        self.silent_for, self.flushed = 0.0, False
        samples = x.astype(np.float32) / 32768
        if self.music_filter:
            self.filtering = True
            try:
                filtered = np.asarray(self.music_filter.run(samples, RATE).samples, dtype=np.float32)
            except Exception as e:
                self._filter_failed(e)  # the current input block can still be recognised unfiltered
            else:
                samples = filtered
                if not len(samples):
                    return
        self._decode(samples)

    def flush(self):
        self._flush_filter()
        if not self.flushed:
            self.flushed = True
            self._decode(np.zeros(int(0.8 * RATE), np.float32), force=True)

    def clear(self):
        """Clear is an explicit acoustic boundary: buffered old speech must not return."""
        self.store.clear()
        self.recent, self.first, self.settled = [], 0, 0
        self.shown, self.live = [], []
        if self.raw is not None:
            self.raw = []
        self.done = 0
        self.partial, self.quiet, self.partial_since = '', 0.0, None
        self.silent_for, self.flushed = 0.0, True
        self.decoded = False
        self.filtering = False  # Clear discards the tail instead of draining pre-Clear speech
        self._reset_filter()
        if self.on_update:
            self.on_update('')
        self.stream = self.rec.create_stream()
        self.result = None

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

    @property
    def count(self):
        """Finished lines since start or the last clear."""
        return self.first + len(self.recent)

    def add_line(self, raw):
        """A finished line, timestamped when it started rather than when it ended."""
        line = (self.partial_since or time.time(), self.format(raw), raw)
        self.recent.append(line)
        self.store.append(line[0], line[1])
        if self.raw is not None:
            self.raw.append(raw)
        if self.count - self.settled > self.DISPLAY_LINES + self.LIVE_LINES:
            self._settle()  # nothing is showing captions: memory stays bounded all the same

    def _settle(self):
        """Freeze the punctuation of all but the newest LIVE_LINES lines, and forget the lines
        the passage no longer needs."""
        while self.count - self.settled > self.LIVE_LINES:
            self.shown.append(self.settle(self.recent[self.settled - self.first]))
            self.settled += 1
        del self.shown[:-self.DISPLAY_LINES]
        keep = max(self.first, self.settled - 1)  # the newest settled line is context for the live ones
        del self.recent[:keep - self.first]
        self.first = keep

    def _decode(self, samples, force=False):
        rec, s = self.rec, self.stream
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
                cut = new.rfind(' ', 0, MAX_LINE) + 1 or len(new)
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
                self.on_update(self.caption_text())

    def caption_text(self):
        """The last few lines and the words still coming, punctuated together as one passage,
        so that phrases split at pauses still read as sentences. Only the newest LIVE_LINES are
        re-punctuated; older lines keep the punctuation they were last shown with, otherwise a
        comma that comes and goes far back in the passage would re-wrap every row."""
        self._settle()
        context = self.recent[self.settled - 1 - self.first][2].split() if self.settled else []
        live = [r for _, _, r in self.recent[self.settled - self.first:]]
        partial = self.partial.strip()
        words = ' '.join(live + ([partial] if partial else [])).split()
        out = self.format(' '.join(context + words)).split() if words else []
        if len(out) == len(context) + len(words):  # punctuation keeps one output word per input word
            out = out[len(context):]
        else:
            out = self.format(' '.join(words)).split()
        self.live = out
        oldest = self.count - self.DISPLAY_LINES  # first line still in the passage
        shown = [s for s in self.shown[max(0, oldest - (self.settled - len(self.shown))):] if s]
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
        out = self.store.lines()
        if self.partial.strip():
            out.append(stamp(self.partial_since or time.time()) + self.format(self.partial))
        return '\n'.join(out)

    def raw_text(self):
        """Complete recognised text for optional benchmark tracing, without timestamps.

        Reading this does not run punctuation again. The normal caption update has
        already paid that cost before the benchmark receives its trace.
        """
        if self.raw is None:
            raise RuntimeError('raw_text() needs Captioner(keep_raw=True)')
        return ' '.join(self.raw + [self.partial.strip()]).strip()


class Translator:
    """Text in any of LANGUAGES to English with M2M100, which needs to know the source
    language: langid.py identifies it from the recognised text (478 of 480 FLEURS test
    sentences right). English passes through unchanged."""

    SURE_CHARS = 12  # shorter text keeps the language of the line before it

    def __init__(self, model, pieces, identify):
        self.model, self.pieces, self.identify = model, pieces, identify

    def translate(self, text, previous=None):
        """(English, language code)."""
        text = text.strip()
        if not text:
            return '', previous
        lang = previous if previous and len(text) < self.SURE_CHARS else self.identify(text)
        if lang == 'en':
            return text, lang
        tokens = [f'__{lang}__'] + self.pieces.encode(text, out_type=str) + ['</s>']
        out = self.model.translate_batch([tokens], target_prefix=[['__en__']], beam_size=1,
                                         max_decoding_length=256)
        return self.pieces.decode(out[0].hypotheses[0][1:]).strip(), lang


SENTENCE_END = '.?!。？！।؟…'
CLOSERS = '"\'”’»)]」』'
CJK = re.compile(r'[぀-ヿ㐀-鿿豈-﫿]')


def ends_sentence(text):
    text = text.rstrip().rstrip(CLOSERS)
    return bool(text) and text[-1] in SENTENCE_END


def join_text(a, b):
    """Two pieces of one sentence; Chinese and Japanese are written without spaces."""
    if not a or not b:
        return a or b
    return a + b if CJK.match(a[-1]) and CJK.match(b[0]) else f'{a} {b}'


class TranslationWorker(threading.Thread):
    """Translates on its own thread, so that recognition never waits for it.

    Lines from the recognizer are gathered into sentences, which translate much better
    than the pieces between pauses; a sentence ends at its final punctuation, after
    MAX_SENTENCE characters, or when the speaker stops (end_sentence). Sentences are
    translated in order and never dropped. While one is still being spoken, its newest
    text is translated at most once per PARTIAL_EVERY_S, so the English follows the speaker
    live. Each request carries the Clear generation it was made in; anything from before a
    Clear is discarded, on screen and in the transcript. on_caption(text, gen) receives the
    English passage; the transcript gets each sentence as "English [language: original]".
    """

    PARTIAL_EVERY_S = 1.0
    MAX_SENTENCE = 300
    DISPLAY_LINES = Captioner.DISPLAY_LINES

    def __init__(self, translator, store, on_caption, on_language=None, on_error=None, gen=0):
        super().__init__(name='nowcaption-translation', daemon=True)
        self.translator, self.store = translator, store
        self.on_caption, self.on_language, self.on_error = on_caption, on_language, on_error
        self.lock = threading.Condition(threading.RLock())
        self.gen = gen
        self.sentence, self.sentence_at = '', None  # the sentence being spoken: finished lines so far
        self.words = ''  # the words still being spoken after them
        self.waiting = deque()  # (gen, time, sentence) to translate
        self.live_text = self.live_done = ''  # the open sentence; the version last translated
        self.partial_at = 0.0
        self.english = deque(maxlen=self.DISPLAY_LINES)
        self.live = ''  # English of the open sentence
        self.language = None
        self.closing = self.stopping = False

    # --- called from the recognition thread ---------------------------------------------------

    def line(self, ts, text):
        with self.lock:
            if not self.sentence:
                self.sentence_at = ts
            self.sentence = join_text(self.sentence, text.strip())
            if ends_sentence(self.sentence) or len(self.sentence) >= self.MAX_SENTENCE:
                self._finish()
            self._update_live()

    def partial_text(self, text):
        with self.lock:
            self.words = text.strip()
            self._update_live()

    def end_sentence(self):
        """The speaker stopped: translate what was said, even without final punctuation."""
        with self.lock:
            if self.sentence:
                self._finish()
                self._update_live()

    def reset(self, gen):
        """Clear: the sentences waiting, the screen and the words in progress belong to the past."""
        with self.lock:
            self.gen = gen
            self.waiting.clear()
            self.sentence, self.words, self.live_text, self.live_done, self.live = '', '', '', '', ''
            self.english.clear()
            self.lock.notify()

    def pending(self):
        """(time, text) of what is not translated yet, as spoken."""
        with self.lock:
            out = [(ts, text) for gen, ts, text in self.waiting if gen == self.gen]
            return out + ([(self.sentence_at, self.sentence)] if self.sentence else [])

    def passage(self):
        with self.lock:
            return ' '.join(list(self.english) + ([self.live] if self.live else []))

    def close(self):
        """Translate what was said, then stop (without blocking the caller)."""
        with self.lock:
            if self.sentence:
                self._finish()
            self.closing = True
            self.lock.notify()

    def stop(self):
        with self.lock:
            self.stopping = True
            self.lock.notify()

    def _finish(self):  # with the lock held
        self.waiting.append((self.gen, self.sentence_at, self.sentence))
        self.sentence, self.sentence_at = '', None
        self.lock.notify()

    def _update_live(self):  # with the lock held
        self.live_text = join_text(self.sentence, self.words)
        self.lock.notify()

    # --- the translation thread ---------------------------------------------------------------

    def run(self):
        try:
            self._run()
        finally:
            self.translator = None  # let go of the translation model as soon as this thread ends
            release_free_memory()

    def _run(self):
        while True:
            with self.lock:
                while True:
                    if self.stopping or (self.closing and not self.waiting):
                        return
                    if self.waiting:
                        item = self.waiting[0]
                        gen, ts, text = item
                        job = 'sentence'
                        break
                    changed = self.live_text != self.live_done
                    wait = self.partial_at + self.PARTIAL_EVERY_S - time.monotonic()
                    if not self.closing and changed and wait <= 0:
                        gen, ts, text, job = self.gen, None, self.live_text, 'live'
                        break
                    self.lock.wait(max(wait, 0.01) if changed else None)
                previous = self.language
            try:
                if job == 'live' and len(text) < self.translator.SURE_CHARS:
                    english, lang = '', None  # too short to tell the language (it may have changed)
                else:
                    english, lang = self.translator.translate(text, previous)
            except Exception as e:
                LOG.exception('Translation failed')
                if self.on_error:
                    self.on_error(e)
                return
            with self.lock:
                if job == 'sentence' and self.waiting and self.waiting[0] is item:
                    self.waiting.popleft()
                if gen != self.gen:
                    continue  # translated before a Clear: never shown, never kept
                if job == 'sentence':
                    if english:
                        self.english.append(english)
                        label = '' if lang in (None, 'en') else f' [{lang}: {text}]'
                        self.store.append(ts, english + label)
                    if not self.waiting:
                        self.live = ''
                        self.live_done = ''  # the open sentence, if any, is translated afresh
                    if lang and lang != self.language:
                        self.language = lang
                        if self.on_language:
                            self.on_language(lang)
                else:
                    self.live_done, self.partial_at = text, time.monotonic()
                    self.live = english
                self.on_caption(self.passage(), gen)


class TranslatingCaptioner(Captioner):
    """Recognises speech in any of LANGUAGES and shows it in English.

    Recognition works as for English captions, but finished lines and the words still
    being spoken go to a TranslationWorker, which shows the English and writes the
    transcript; the spoken language is not shown. The recognizer writes punctuation itself.
    The recognizer picks the spoken language when a stream starts, so after the speaker
    stops (digital silence, or NEW_STREAM_QUIET_S without new words) the next speech gets
    a fresh stream, in case it is in another language. gen() returns the Clear generation.
    """

    NEW_STREAM_QUIET_S = 3.0

    def __init__(self, recognizer, translation, gen, **kwargs):
        super().__init__(recognizer, None, on_update=self._changed, **kwargs)
        self.translation, self.gen = translation, gen

    def add_line(self, raw):
        self.translation.line(self.partial_since or time.time(), raw)
        self.first += 1  # the lines live in the TranslationWorker, not here

    def caption_text(self):
        return ''  # the TranslationWorker shows the English

    def _changed(self, _):
        self.translation.partial_text(self.partial)

    def _decode(self, samples, force=False):
        super()._decode(samples, force)
        if self.done and not self.partial.strip() and self.quiet >= self.NEW_STREAM_QUIET_S:
            self._new_stream()

    def flush(self):
        super().flush()
        if self.done:
            self._new_stream()

    def _new_stream(self):
        self.translation.end_sentence()
        self.stream, self.done, self.result, self.quiet = self.rec.create_stream(), 0, None, 0.0

    def continue_from(self, old):
        old.commit_partial()
        self.store, self.raw = old.store, old.raw

    def clear(self):
        with self.translation.lock:  # no translation from before the Clear may land after it
            self.translation.reset(self.gen())
            super().clear()

    def transcript(self):
        """Translated sentences, then what is still being translated or spoken, as spoken."""
        out = self.store.lines() + [stamp(ts) + text for ts, text in self.translation.pending()]
        if self.partial.strip():
            out.append(stamp(self.partial_since or time.time()) + self.partial.strip())
        return '\n'.join(out)


class RecognitionWorker(threading.Thread):
    """The only thread that touches the speech models.

    Other threads talk to it with the methods below; it reports back through
      on_event(kind, value), called on this thread (overload may also come from capture):
      ('ready', None)            models loaded
      ('caption', (gen, text))   new caption passage; gen is the clear() generation it belongs to
      ('snapshot', (fn, text))   reply to snapshot(fn)
      ('snapshot-error', (fn, message)) reply to snapshot(..., on_error=fn) when export fails
      ('warning', message)       something degraded but captioning continues
      ('error', message)         recognition stopped; restart() or a new source tries again
      ('load-error', message)    the models could not be loaded; a new source or restart() retries
      ('overload', seconds)      audio arrived faster than it could be recognised and was dropped
      ('recognition-ok', gen)    the new stream processed audio successfully
      ('music-filter', on)       the music filter is now on or off (off if it could not load)
      ('translation', state)     translation to English is 'loading', True (on) or False (off)
      ('translation-error', message)  translation could not start, or stopped after an error
      ('translation-language', code)  the spoken language changed (a key of LANGUAGES)
    Audio is tagged with a source generation; blocks from an older source are ignored, so a
    capture thread that is still shutting down can never feed the new source's captions.
    Pending audio is bounded at enqueue time; commands are never dropped for lack of space.
    """

    MAX_BACKLOG = 50  # at most 2 s of pending 40 ms capture blocks, excluding one in-flight call
    REPORT_DROPPED_S = 2.0  # say so while the worker is stalled, after this much audio is dropped

    def __init__(self, on_event, load=None, on_trace=None, load_filter=None, load_translation=None):
        super().__init__(name='nowcaption-recognition', daemon=True)
        self.on_event = on_event
        self.on_trace = on_trace  # optional complete-text trace, used only by benchmarks
        self.load = load or (lambda: load_models(lambda m: self.on_event('warning', m)))
        self.load_filter = load_filter or load_music_filter
        self.load_translation = load_translation or globals()['load_translation']
        self.music_filter, self.music_filter_on = None, False
        # translation: wanted, its models while loaded, loading in the background, its thread
        self.translate_on, self.translation, self.translation_loading = False, None, False
        self.translation_worker = None
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
                for i, (kind, value) in enumerate(self.inbox):
                    if kind == 'audio':
                        del self.inbox[i]
                        self.backlog -= 1
                        self.dropped += duration(value[1])
                        break
                self.discontinuity = True
                if self.dropped >= self.REPORT_DROPPED_S:
                    report, self.dropped = self.dropped, 0
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

    def snapshot(self, fn, on_error=None):
        with self.lock:
            if self.stopping or not self.is_alive():
                raise RuntimeError('The recognition worker is not running')
            self.inbox.append(('snapshot', (fn, on_error)))
            self.lock.notify()

    def set_music_filter(self, on):
        """Turn the optional music filter on or off; it is loaded the first time it is needed."""
        self._put('music-filter', bool(on))

    def set_translation(self, on):
        """Turn translation to English on or off. Its models load in the background, while
        English captions carry on, and are released when it is turned off."""
        self._put('translate', bool(on))

    def restart(self, recover_only=False, source_gen=None):
        """Manual restart, or a guarded automatic retry for a failed source generation."""
        self._put('recover' if recover_only else 'restart', source_gen)

    def stop(self):
        with self.lock:
            self.stopping = True
            self.inbox.clear()
            self.backlog = 0
            self.lock.notify()
        if self.translation_worker:
            self.translation_worker.stop()

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
                    self.on_event('overload', dropped)
            elif kind == 'clear':
                self._safely(self._handle, kind, value)  # generations apply even when loading failed
            elif kind in ('snapshot', 'music-filter', 'translate', 'translate-loaded', 'translate-failed'):
                self._safely(self._handle, kind, value)
            elif loaded:
                if kind != 'source' or value == self.source_gen:
                    self._safely(self._handle, kind, value)
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
        if isinstance(self.captioner, TranslatingCaptioner):
            self.on_event('translation', True)
        return True

    def _new_captioner(self):
        rec, punct = self.models
        old = self.captioner
        common = dict(on_warning=lambda m: self.on_event('warning', m), on_filter_error=self._filter_failed,
                      store=old.store if old else None, keep_raw=self.on_trace is not None)
        if self.translate_on and self.translation:
            if self.translation_worker is None:
                self.translation_worker = TranslationWorker(
                    self.translation[1], common['store'] or TranscriptStore(),
                    on_caption=lambda text, gen: self.on_event('caption', (gen, text)),
                    on_language=lambda lang: self.on_event('translation-language', lang),
                    on_error=lambda e: self._put('translate-failed', f'Translation stopped after an error: {e}'),
                    gen=self.ui_gen)
                self.translation_worker.start()
            common['store'] = self.translation_worker.store
            new = TranslatingCaptioner(self.translation[0], self.translation_worker, lambda: self.ui_gen, **common)
        else:
            new = Captioner(rec, punct, on_update=self._caption, **common)
        if old and type(old) is type(new):  # keep the session transcript when recovering from an error
            new.continue_from(old)
        elif old:  # switching translation on or off: the transcript carries on, the screen starts afresh
            old.commit_partial()
        new.set_music_filter(self.music_filter if self.music_filter_on else None)
        self.captioner = new
        self.broken = False
        self.confirmed = False
        if old and isinstance(new, TranslatingCaptioner):
            self.on_event('caption', (self.ui_gen, self.translation_worker.passage()))
        elif old:
            self._caption(new.caption_text())

    def _switch_translation(self):
        """Use the captioner the current setting asks for, and release translation when it is off."""
        if self.translate_on and self.translation:
            if not isinstance(self.captioner, TranslatingCaptioner):
                self._new_captioner()
            self.on_event('translation', True)
            return
        if isinstance(self.captioner, TranslatingCaptioner):
            self._new_captioner()
        if self.translation_worker:
            self.translation_worker.close()  # it finishes the lines still waiting, then lets go of the models
            self.translation_worker = None
        if self.translation:
            self.translation = None
            gc.collect()  # the replaced captioner refers to itself; free its 1 GB recognizer now
            release_free_memory()
        self.on_event('translation', False)

    def _load_translation(self):
        if self.translation_loading:
            return
        self.translation_loading = True

        def load():
            try:
                models = self.load_translation()
            except Exception as e:
                LOG.exception('Could not load translation')
                self._put('translate-failed', f'Translation could not be loaded: {e}')
            else:
                self._put('translate-loaded', models)
        threading.Thread(target=load, name='nowcaption-translation-load', daemon=True).start()

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
            callback, on_error = value
            try:
                text = self.captioner.transcript() if self.captioner else ''
            except Exception as e:
                LOG.exception('Could not prepare the transcript')
                self.on_event('snapshot-error', (on_error, str(e)))
            else:
                self.on_event('snapshot', (callback, text))
        elif kind == 'music-filter':
            if value and self.music_filter is None:
                try:
                    self.music_filter = self.load_filter()
                except Exception as e:
                    LOG.exception('Could not load the music filter')
                    self.on_event('warning', f'The music filter could not be loaded: {e}')
                    value = False
            self.music_filter_on = value
            if self.captioner:
                self.captioner.set_music_filter(self.music_filter if value else None)
            self.on_event('music-filter', self.music_filter_on)
        elif kind == 'translate':
            self.translate_on = value
            if value and self.translation is None:
                self.on_event('translation', 'loading')
                self._load_translation()
            elif self.captioner:
                self._switch_translation()
        elif kind == 'translate-loaded':
            self.translation_loading = False
            self.translation = value if self.translate_on else None
            if self.captioner:
                self._switch_translation()
        elif kind == 'translate-failed':
            self.translation_loading = False
            self.translate_on = False
            self.on_event('translation-error', value)
            if self.captioner:
                self._switch_translation()
            else:
                self.on_event('translation', False)
        elif kind == 'restart':
            self._safely(self._new_captioner)
        elif kind == 'recover' and self.broken and (value is None or value == self.source_gen):
            # An automatic timer may already have queued its command when a user
            # recovers manually. Never replace a healthy or newer stream for it.
            self._safely(self._new_captioner)

    def _filter_failed(self, error):
        self.music_filter, self.music_filter_on = None, False
        self.on_event('warning', f'The music filter was turned off after an error: {error}')
        self.on_event('music-filter', False)

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
