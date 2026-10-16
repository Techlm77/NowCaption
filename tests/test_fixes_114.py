#!/usr/bin/env python3
"""Regressions for 1.1.4: the transcript lives in an unnamed temporary file, and the
caption pipeline keeps only the lines the caption box still needs. No models or GTK.
"""
import errno
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nowcaption import engine
import test_reliability as fixtures

TS = 1_700_000_000.0


class TranscriptStoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / 'nowcaption'

    def test_lines_go_to_a_private_file_without_a_name(self):
        store = engine.TranscriptStore(self.folder)
        store.append(TS, 'Hello there.')
        store.append(TS + 61, 'Café, naïve — 字幕.')
        self.assertEqual(store.lines(), [engine.stamp(TS) + 'Hello there.', engine.stamp(TS + 61) + 'Café, naïve — 字幕.'])
        self.assertEqual(store.count, 2)
        self.assertIsNone(store.memory)
        info = os.fstat(store.file.fileno())
        self.assertEqual(info.st_nlink, 0)  # no name: nothing else can open it, and it goes away with the app
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)

    def test_clear_frees_the_old_text_on_disk(self):
        store = engine.TranscriptStore(self.folder)
        store.append(TS, 'Private words.')
        store.clear()
        self.assertEqual(os.fstat(store.file.fileno()).st_size, 0)
        self.assertEqual((store.lines(), store.count), ([], 0))
        store.append(TS, 'New words.')
        self.assertEqual(store.lines(), [engine.stamp(TS) + 'New words.'])

    def test_cache_folder_follows_xdg_cache_home(self):
        with patch.dict(os.environ, XDG_CACHE_HOME=str(self.folder.parent)):
            self.assertEqual(engine.cache_dir(), self.folder)
            store = engine.TranscriptStore()
        self.assertIsNotNone(store.file)

    def test_an_unusable_cache_folder_keeps_the_transcript_in_memory(self):
        self.folder.parent.mkdir(exist_ok=True)
        self.folder.write_text('a file where the folder should be')
        store = engine.TranscriptStore(self.folder)
        self.assertIsNone(store.file)
        store.append(TS, 'Still kept.')
        self.assertEqual(store.lines(), [engine.stamp(TS) + 'Still kept.'])
        store.clear()
        self.assertEqual(store.lines(), [])

    def test_a_failed_write_moves_every_line_to_memory(self):
        store = engine.TranscriptStore(self.folder)
        store.append(TS, 'one')
        real_pwrite, calls = os.pwrite, []

        def full_disk(fd, data, offset):
            calls.append(offset)
            if len(calls) == 2:
                real_pwrite(fd, data[:3], offset)  # a partial line must not appear
                raise OSError(errno.ENOSPC, 'No space left on device')
            return real_pwrite(fd, data, offset)

        with patch.object(engine.os, 'pwrite', full_disk):
            store.append(TS, 'two')
            store.append(TS, 'three')
        self.assertIsNone(store.file)
        self.assertEqual(store.lines(), [engine.stamp(TS) + w for w in ('one', 'two', 'three')])
        self.assertEqual(store.count, 3)

    def test_late_translation_keeps_its_speech_order_across_days_and_clock_changes(self):
        store = engine.TranscriptStore(self.folder)
        token = store.reserve(TS, 'older source')
        store.append(TS + 86401, 'newer English')
        store.resolve(token, 'older translation')
        self.assertEqual([line.split('] ', 1)[1] for line in store.lines()],
                         ['older translation', 'newer English'])
        store.append(TS - 1, 'speech after the clock moved back')
        self.assertTrue(store.lines()[-1].endswith('speech after the clock moved back'))

    def test_pending_source_is_replaced_once_without_changing_speech_order(self):
        store = engine.TranscriptStore(self.folder)
        token = store.reserve(TS, 'palabras anteriores')
        store.append(TS + 2, 'new English')
        self.assertEqual([line.split('] ', 1)[1] for line in store.lines()],
                         ['palabras anteriores', 'new English'])
        store.resolve(token, 'earlier words [es: palabras anteriores]')
        store.resolve(token, 'duplicate result')
        self.assertEqual([line.split('] ', 1)[1] for line in store.lines()],
                         ['earlier words [es: palabras anteriores]', 'new English'])
        self.assertEqual(store.count, 2)
        self.assertFalse(store.pending)
        self.assertIsNone(store.memory)

    def test_clear_discards_pending_source_and_late_results(self):
        store = engine.TranscriptStore(self.folder)
        token = store.reserve(TS, 'private source')
        store.clear()
        store.resolve(token, 'late private translation')
        self.assertEqual((store.lines(), store.count), ([], 0))


def talk(cap, words):
    """Speak some words, then pause long enough to finish the line."""
    for _ in range(words):
        cap.feed(fixtures.SPEECH)
    for _ in range(9):
        cap.feed(fixtures.SILENCE)


class BoundedCaptionerTests(unittest.TestCase):
    def test_a_long_session_keeps_only_what_the_caption_box_needs(self):
        script = [f'word{i} ' for i in range(3000)]
        updates = []
        cap = engine.Captioner(fixtures.FakeRecognizer(script), on_update=updates.append)
        for _ in range(500):
            talk(cap, 6)
        self.assertEqual(cap.count, 500)
        self.assertLessEqual(len(cap.recent), cap.LIVE_LINES + 1)
        self.assertLessEqual(len(cap.shown), cap.DISPLAY_LINES)
        lines = cap.transcript().splitlines()
        self.assertEqual(len(lines), 500)
        self.assertTrue(lines[0].endswith('Word0 word1 word2 word3 word4 word5'))
        self.assertIn('word2999', lines[-1])
        self.assertTrue(updates[-1].endswith('word2999'))
        self.assertIsNone(cap.raw)  # raw history is kept only for benchmark tracing

    def test_without_caption_updates_memory_is_bounded_too(self):
        cap = engine.Captioner(fixtures.FakeRecognizer([f'word{i} ' for i in range(1000)]))
        for _ in range(300):
            talk(cap, 3)
        self.assertEqual(cap.count, 300)
        self.assertLessEqual(len(cap.recent), cap.DISPLAY_LINES + cap.LIVE_LINES + 1)
        self.assertEqual(len(cap.transcript().splitlines()), 300)

    def test_recovery_continues_the_same_transcript_file(self):
        events = fixtures.Events()
        worker = engine.RecognitionWorker(events, load=lambda: (fixtures.FakeRecognizer(['before ', 'after ']), None))
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        store = worker.captioner.store
        worker.feed(0, fixtures.SPEECH)
        events.snapshot(worker)
        worker.restart()
        worker.feed(0, fixtures.SPEECH)
        self.assertIs(worker.captioner.store, store)
        text = events.snapshot(worker)
        self.assertEqual(text.lower().count('before'), 1)
        self.assertIn('after', text.lower())

    def test_tracing_keeps_the_complete_raw_text(self):
        events = fixtures.Events()
        traces = []
        worker = engine.RecognitionWorker(events, load=lambda: (fixtures.FakeRecognizer(['one ', 'two ']), None),
                                          on_trace=traces.append)
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        for _ in range(2):
            worker.feed(0, fixtures.SPEECH)
        events.snapshot(worker)
        self.assertEqual(traces[-1], 'one two')
        with self.assertRaises(RuntimeError):
            engine.Captioner(fixtures.FakeRecognizer()).raw_text()


class ReleaseMemoryTests(unittest.TestCase):
    def test_freed_memory_is_returned_where_glibc_is_available(self):
        engine._malloc_trim = None
        self.assertIsInstance(engine.release_free_memory(), bool)
        self.assertIsNotNone(engine._malloc_trim)

    def test_without_glibc_it_does_nothing(self):
        with patch.object(engine.ctypes, 'CDLL', side_effect=OSError('no libc.so.6')):
            engine._malloc_trim = None
            self.assertFalse(engine.release_free_memory())
            self.assertFalse(engine.release_free_memory())
        engine._malloc_trim = None


if __name__ == '__main__':
    unittest.main(verbosity=2)
