#!/usr/bin/env python3
"""Quitting waits for the recognition worker and the capture to leave native code.

Python 3.12 and 3.13 end daemon threads that are still in native code when the interpreter
exits with pthread_exit, and sherpa-onnx's C++ code then aborts the process ("terminate called
without an active exception"). Runs unchanged application methods with fake threads; main()
itself is tested in test_app.py, which needs GTK.
"""
import threading
import time
import unittest
from types import SimpleNamespace as NS

from test_regressions import app_with_fakes


def busy_worker(seconds):
    """A thread that stays in a 'native call' for the given time once started."""
    stop = threading.Event()
    thread = threading.Thread(target=lambda: time.sleep(seconds), daemon=True)
    thread.stop = stop.set
    thread.start()
    return thread


class QuitTests(unittest.TestCase):
    def test_the_worker_finishing_its_decode_is_waited_for(self):
        a = app_with_fakes()
        a.worker = busy_worker(0.2)  # one decode, about 26 ms with the real model
        a.capture.wait_stopped = lambda timeout: True
        start = time.monotonic()
        self.assertTrue(a.threads_finished(3))
        self.assertFalse(a.worker.is_alive())
        self.assertLess(time.monotonic() - start, 2)

    def test_a_stalled_worker_is_reported_after_the_timeout(self):
        a = app_with_fakes()
        a.worker = busy_worker(5)  # a model still loading, for example
        a.capture.wait_stopped = lambda timeout: True
        start = time.monotonic()
        self.assertFalse(a.threads_finished(0.2))
        self.assertLess(time.monotonic() - start, 1)

    def test_a_capture_still_shutting_down_is_reported(self):
        a = app_with_fakes()
        a.worker = None  # the models never loaded
        waited = []
        a.capture.wait_stopped = lambda timeout: waited.append(timeout) or False
        self.assertFalse(a.threads_finished(3))
        self.assertEqual(waited, [3])

    def test_window_shutdown_still_never_waits(self):
        a = app_with_fakes()
        a.worker = NS(stop=lambda: None, join=lambda *a, **kw: self.fail('the window thread joined the worker'))
        a.capture.wait_stopped = lambda timeout: self.fail('the window thread waited for the capture')
        a.on_shutdown(None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
