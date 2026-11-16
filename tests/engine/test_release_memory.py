#!/usr/bin/env python3
"""Giving freed memory back to the system (1.1.4); tests/test_memory.py measures the real models.
"""
import unittest
from unittest.mock import patch

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine


class ReleaseMemoryTests(unittest.TestCase):
    def test_freed_memory_is_returned_where_glibc_is_available(self):
        engine.memory._malloc_trim = None
        self.assertIsInstance(engine.release_free_memory(), bool)
        self.assertIsNotNone(engine.memory._malloc_trim)

    def test_without_glibc_it_does_nothing(self):
        with patch.object(engine.memory.ctypes, 'CDLL', side_effect=OSError('no libc.so.6')):
            engine.memory._malloc_trim = None
            self.assertFalse(engine.release_free_memory())
            self.assertFalse(engine.release_free_memory())
        engine.memory._malloc_trim = None


if __name__ == '__main__':
    unittest.main(verbosity=2)
