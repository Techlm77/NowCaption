#!/usr/bin/env python3
"""Finding the speech models: incomplete model folders are never used, and a missing punctuation
model only turns punctuation off (the reliability review, finding 8).
"""
import os
import unittest
from pathlib import Path

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from support.fakes import FakeRecognizer


class ModelFileTests(unittest.TestCase):
    """Finding 8: incomplete model folders are never used."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = os.environ.get('NOWCAPTION_MODELS')

    def tearDown(self):
        if self.old is None:
            os.environ.pop('NOWCAPTION_MODELS', None)
        else:
            os.environ['NOWCAPTION_MODELS'] = self.old
        self.tmp.cleanup()

    def make(self, sizes):
        d = self.root / engine.ASR_DIR
        d.mkdir(parents=True, exist_ok=True)
        for name, size in zip(engine.ASR_FILES, sizes, strict=True):
            (d / name).write_bytes(b'x' * size)

    def test_incomplete_explicit_model_folder_is_rejected(self):
        self.make([10, 10, 0, 10])  # a cut-off download
        os.environ['NOWCAPTION_MODELS'] = str(self.root)
        with self.assertRaises(FileNotFoundError):
            engine.models_dir()

    def test_complete_explicit_model_folder_is_used(self):
        self.make([10, 10, 10, 10])
        os.environ['NOWCAPTION_MODELS'] = str(self.root)
        self.assertEqual(engine.models_dir(), self.root)

    def test_missing_punctuation_model_still_loads_with_a_warning(self):
        warnings = []
        real = (engine.models.load_recognizer, engine.models.load_punctuation)
        def no_punct():
            raise FileNotFoundError('punctuation model is missing')
        try:
            engine.models.load_recognizer, engine.models.load_punctuation = (lambda: FakeRecognizer()), no_punct
            rec, punct = engine.load_models(warnings.append)
        finally:
            engine.models.load_recognizer, engine.models.load_punctuation = real
        self.assertIsInstance(rec, FakeRecognizer)
        self.assertIsNone(punct)
        self.assertIn('Punctuation is off', warnings[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
