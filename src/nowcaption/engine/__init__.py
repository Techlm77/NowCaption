"""The speech pipeline and transcript storage: audio capture, speech recognition (a sherpa-onnx
streaming transducer plus a punctuation model), caption text and the history files.

Uses words only, and no gi but capture.py's GStreamer, imported when a Capture is made, so the
engine imports with NumPy alone (CONTRIBUTING.md).
"""
from .captioner import MAX_LINE, PAUSE_S, RATE, WARMUP_S, Captioner
from .capture import DESKTOP, MICROPHONE, Capture
from .history import (
    HISTORY_CHOICES,
    HISTORY_DAYS,
    history_cutoff,
    history_dir,
    history_file,
    history_files,
    history_span,
    history_title,
    prune_history,
    session_heading,
)
from .memory import release_free_memory
from .models import (
    ASR_DIR,
    ASR_FILES,
    PUNCT_DIR,
    PUNCT_FILES,
    complete,
    load_models,
    load_punctuation,
    load_recognizer,
    models_dir,
)
from .transcript import TranscriptStore, cache_dir, stamp
from .worker import RecognitionWorker

__all__ = [
    'MAX_LINE', 'PAUSE_S', 'RATE', 'WARMUP_S', 'Captioner',
    'DESKTOP', 'MICROPHONE', 'Capture',
    'HISTORY_CHOICES', 'HISTORY_DAYS', 'history_cutoff', 'history_dir', 'history_file',
    'history_files', 'history_span', 'history_title', 'prune_history', 'session_heading',
    'release_free_memory',
    'ASR_DIR', 'ASR_FILES', 'PUNCT_DIR', 'PUNCT_FILES', 'complete', 'load_models',
    'load_punctuation', 'load_recognizer', 'models_dir',
    'TranscriptStore', 'cache_dir', 'stamp',
    'RecognitionWorker',
]
