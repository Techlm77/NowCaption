#!/usr/bin/env python3
"""Recognition slower than real time catches up with live audio one gap at a time, and every
new stream hears silence before its first audio.

Runs the real recognition worker with controlled models. No GTK, audio server or
downloaded models. The screen-change fix is tested in test_window.py, which needs GTK.
"""
import threading
import unittest

import numpy as np

from nowcaption import engine
from test_reliability import Events, FakeRecognizer, FakeStream

BLOCK = np.full(640, 1000, dtype=np.int16).tobytes()  # 40 ms, as the capture delivers it


class SustainedOverloadTests(unittest.TestCase):
    def tearDown(self):
        if getattr(self, 'worker', None):
            self.worker.stop()

    def test_a_full_backlog_is_skipped_at_once_and_reported_once(self):
        reports = []
        worker = engine.RecognitionWorker(lambda kind, value: reports.append((kind, value)))  # not started
        for _ in range(worker.MAX_BACKLOG + 1):
            worker.feed(0, BLOCK)
        self.assertEqual(worker.backlog, 1, 'recognition should go on from the newest audio')
        self.assertEqual(len(reports), 1)
        kind, seconds = reports[0]
        self.assertEqual(kind, 'overload')
        self.assertAlmostEqual(seconds, worker.MAX_BACKLOG * 0.04)

    def test_half_real_time_recognition_restarts_once_per_catch_up_not_per_block(self):
        # Two capture blocks arrive while each one is recognised, as on a CPU that is too slow.
        # 1.1.5 dropped one block at a time: captions stayed 2 s late, the stream restarted at
        # almost every block, and each restart was announced as "skipped 0.0 s of audio".
        total, fed, all_fed = 1000, [1], threading.Event()
        worker_box = []

        class Stream(FakeStream):
            def accept_waveform(self, rate, samples):
                super().accept_waveform(rate, samples)
                if np.any(samples):
                    for _ in range(2):
                        if fed[0] < total:
                            fed[0] += 1
                            worker_box[0].feed(1, BLOCK)
                    if fed[0] >= total:
                        all_fed.set()

        class Rec(FakeRecognizer):
            def create_stream(self):
                return Stream(self)

        events = Events()
        self.worker = engine.RecognitionWorker(events, load=lambda: (Rec(['w '] * 5000), None))
        worker_box.append(self.worker)
        self.worker.start()
        events.wait('ready')
        self.worker.set_source(1)
        events.snapshot(self.worker)  # the source change has been handled
        restarts_before = self.worker.stream_gen
        self.worker.feed(1, BLOCK)
        self.assertTrue(all_fed.wait(10))
        events.snapshot(self.worker)  # every block fed before this was recognised or skipped
        restarts = self.worker.stream_gen - restarts_before
        overloads = [v for k, v in events.seen if k == 'overload']
        self.assertTrue(overloads, 'falling behind was never reported')
        self.assertLessEqual(len(overloads), total // self.worker.MAX_BACKLOG)
        self.assertLessEqual(restarts, len(overloads))
        self.assertGreaterEqual(min(overloads), 1.0, 'a notice would read "skipped 0.0 s"')



class WarmUpTests(unittest.TestCase):
    # NowCaption's own model (1.1.6) shows words up to 80 ms later when speech starts on a
    # stream's very first sample, so each new stream gets silence in front of its first block.
    class Rec(FakeRecognizer):
        def __init__(self, script):
            super().__init__(script)
            self.accepted = []

        def create_stream(self):
            rec = self

            class Stream(FakeStream):
                def accept_waveform(self, rate, samples):
                    super().accept_waveform(rate, samples)
                    rec.accepted.append(np.asarray(samples))
            return Stream(self)

    def test_the_first_block_of_a_stream_comes_after_the_warm_up_silence(self):
        rec = self.Rec(['hello '] * 10)
        cap = engine.Captioner(rec)
        cap.feed(BLOCK)
        cap.feed(BLOCK)
        warm = int(engine.WARMUP_S * engine.RATE)
        self.assertEqual(len(rec.accepted), 2, 'the silence goes in the same call as the first block')
        first, second = rec.accepted
        self.assertEqual(len(first), warm + 640)
        self.assertFalse(first[:warm].any())
        self.assertTrue(first[warm:].all())
        self.assertEqual(len(second), 640)

    def test_clear_starts_a_new_stream_that_is_warmed_up_again(self):
        rec = self.Rec(['hello '] * 10)
        cap = engine.Captioner(rec)
        cap.feed(BLOCK)
        cap.clear()
        cap.feed(BLOCK)
        self.assertEqual([len(x) for x in rec.accepted], [int(engine.WARMUP_S * engine.RATE) + 640] * 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
