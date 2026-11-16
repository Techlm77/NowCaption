"""Finding and loading the speech models: the streaming transducer and the punctuation model.

sherpa-onnx is imported only when a model loads, so tests and tools can import the engine
without it. Depends on nothing else in NowCaption.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path

LOG = logging.getLogger(__name__)

ASR_DIR = 'nowcaption-fastconformer-en-80ms-cc-by-v4'
ASR_FILES = ('tokens.txt', 'encoder.int8.onnx', 'decoder.int8.onnx', 'joiner.int8.onnx')
PUNCT_DIR = 'sherpa-onnx-online-punct-en-2024-08-06'
PUNCT_FILES = ('model.int8.onnx', 'bpe.vocab')


def complete(folder: Path, names: Iterable[str]) -> bool:
    """All named files exist in folder and are not empty (a cut-off download leaves empty or missing files)."""
    return all((folder / n).is_file() and (folder / n).stat().st_size > 0 for n in names)


def models_dir() -> Path:
    """$NOWCAPTION_MODELS, the Flatpak/system install, or ./models in a checkout."""
    explicit = os.environ.get('NOWCAPTION_MODELS')
    if explicit:
        if not complete(Path(explicit) / ASR_DIR, ASR_FILES):
            raise FileNotFoundError(f'NOWCAPTION_MODELS={explicit} does not contain a complete {ASR_DIR}')
        return Path(explicit)
    for c in (Path(__file__).resolve().parents[3] / 'models', Path('/app/share/nowcaption/models'),
              Path('/usr/local/share/nowcaption/models'), Path('/usr/share/nowcaption/models')):
        if complete(c / ASR_DIR, ASR_FILES):
            return c
    raise FileNotFoundError('Speech model not found or incomplete. Run tools/fetch-models.sh '
                            'or set NOWCAPTION_MODELS.')


def load_recognizer(threads: int = 1):
    """NowCaption's streaming transducer as a sherpa-onnx OnlineRecognizer, decoding greedily on
    this many threads. Raises FileNotFoundError if the model is missing (see models_dir)."""
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


def load_models(on_warning: Callable[[str], None] | None = None):
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
