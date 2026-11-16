"""Stand-ins for what the tests do not run for real: the speech models (recognizer and
punctuation), the recognition worker's events, GLib's timers for the application methods
app_loader compiles, GStreamer for the capture, and programs that scripts call.

The audio blocks are 100 ms; tests that need the capture's 40 ms blocks make their own.
"""
import queue
import stat
import threading
import time
from types import SimpleNamespace as NS

import numpy as np

SPEECH = np.full(1600, 1000, dtype=np.int16).tobytes()  # 100 ms of non-silent audio
SILENCE = np.zeros(1600, dtype=np.int16).tobytes()


class FakeStream:
    def __init__(self, rec):
        self.rec, self.text = rec, ''
        self.ready = False

    def accept_waveform(self, rate, samples):
        rec = self.rec
        rec.threads.add(threading.current_thread().name)
        with rec.lock:
            rec.active += 1
            rec.max_active = max(rec.max_active, rec.active)
        try:
            if rec.fail:
                raise RuntimeError('injected recognizer failure')
            if rec.slow:
                time.sleep(rec.slow)
            if np.any(samples) and rec.script:
                self.text += rec.script.pop(0)
            self.ready = True
        finally:
            with rec.lock:
                rec.active -= 1


class FakeRecognizer:
    """Appends the next scripted word for each block of speech."""

    def __init__(self, script=()):
        self.script, self.text = list(script), ''
        self.fail, self.slow = False, 0
        self.threads, self.lock, self.active, self.max_active = set(), threading.Lock(), 0, 0

    def create_stream(self):
        return FakeStream(self)

    def is_ready(self, stream):
        return stream.ready

    def decode_stream(self, stream):
        stream.ready = False

    def get_result(self, stream):
        return stream.text

    def reset(self, stream):
        stream.text = ''
        stream.ready = False


class StrippedRecognizer(FakeRecognizer):
    def get_result(self, stream):
        return super().get_result(stream).strip()  # as load_recognizer() returns native results


class FakePunct:
    def __init__(self, fail=False):
        self.fail = fail

    def add_punctuation_with_case(self, text):
        if self.fail:
            raise RuntimeError('injected punctuation failure')
        return text[:1].upper() + text[1:] + '.'


class FlakyPunctuation:
    """Moves its commas whenever the passage grows, like the real model reconsidering."""
    def add_punctuation_with_case(self, text):
        words = text.split()
        return ' '.join(w + (',' if (i + len(words)) % 3 == 0 else '') for i, w in enumerate(words))


class Events:
    """Collects worker events; wait() blocks until one matching predicate arrives."""

    def __init__(self):
        self.q, self.seen = queue.Queue(), []

    def __call__(self, kind, value):
        self.q.put((kind, value))

    def wait(self, kind, pred=lambda v: True, timeout=3):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                k, v = self.q.get(timeout=end - time.monotonic())
            except queue.Empty:
                break
            self.seen.append((k, v))
            if k == kind and pred(v):
                return v
        raise AssertionError(f'no {kind!r} event; saw {self.seen[-10:]}')

    def snapshot(self, worker):
        box = []
        worker.snapshot(box.append)
        fn, text = self.wait('snapshot')
        fn(text)
        return box[0]


class Timers:
    def __init__(self):
        self.pending, self.delays, self.serial = {}, [], 0

    def timeout_add_seconds(self, seconds, callback, *args):
        self.serial += 1
        self.pending[self.serial] = lambda: callback(*args)
        self.delays.append(seconds)
        return self.serial

    def source_remove(self, timer):
        return self.pending.pop(timer, None) is not None

    def fire(self, timer):
        return self.pending.pop(timer)()


class FakeGst:
    State = NS(PLAYING='playing', NULL='null')
    StateChangeReturn = NS(FAILURE='failure', SUCCESS='success')
    MessageType = NS(ERROR=1, EOS=2)
    MSECOND = 1_000_000
    Structure = NS(new_from_string=lambda s: s)

    def __init__(self, behaviour):
        self.behaviour, self.pipelines = behaviour, []
        self.provider = None  # the PulseAudio device provider, if a test gives one
        self.DeviceProviderFactory = NS(
            get_by_name=lambda name: self.provider if name == 'pulsedeviceprovider' else None)

    def init(self, arg):
        pass

    def parse_launch(self, description):
        p = FakePipeline(self.behaviour)
        self.pipelines.append(p)
        return p


class FakeDevice:
    def __init__(self, device_class, name, default):
        self.device_class, self.name, self.default = device_class, name, default

    def get_device_class(self):
        return self.device_class

    def get_property(self, name):
        assert name == 'internal-name', name
        return self.name

    def get_properties(self):
        fields = {'is-default': self.default}
        return NS(has_field=lambda f: f in fields, get_value=fields.get)


class FakeDeviceProvider:
    """Like GStreamer's PulseAudio device provider: the sound server's outputs (sinks) and sources,
    the defaults marked only while it is started, as the real one does."""

    def __init__(self, sinks=(), sources=(), default_sink=None, default_source=None, can_start=True):
        self.sinks, self.sources = list(sinks), list(sources)
        self.default_sink, self.default_source, self.can_start = default_sink, default_source, can_start
        self.running = self.starts = 0

    def start(self):
        if not self.can_start:
            return False
        self.running += 1
        self.starts += 1
        return True

    def stop(self):
        self.running -= 1

    def get_devices(self):
        started = self.running > 0
        return ([FakeDevice('Audio/Source', n, started and n == self.default_source) for n in self.sources] +
                [FakeDevice('Audio/Sink', n, started and n == self.default_sink) for n in self.sinks])


class FakePipeline:
    def __init__(self, behaviour):
        self.behaviour, self.state, self.pulls = behaviour, None, 0
        self.message = None

    def get_by_name(self, name):
        return self

    def get_bus(self):
        return self

    def set_property(self, name, value):
        if name == 'device':
            self.device = value

    def get_property(self, name):
        # like pulsesrc: a device that doesn't exist is silently replaced by the default
        return 'default.monitor' if self.behaviour == 'missing' else self.device

    def set_state(self, state):
        self.state = state
        return 'failure' if self.behaviour == 'refuse' and state == 'playing' else 'success'

    def pop_filtered(self, kinds):
        m, self.message = self.message, None
        return m

    def is_eos(self):
        return self.behaviour == 'eos'

    def try_pull_sample(self, timeout):
        self.pulls += 1
        if self.state == 'null':
            return None
        if self.behaviour == 'eos':
            return None
        if self.behaviour == 'error' and self.pulls == 3:
            self.message = NS(type=FakeGst.MessageType.ERROR,
                              parse_error=lambda: (NS(message='device disappeared'), None))
        time.sleep(0.002)
        data = SPEECH
        return NS(get_buffer=lambda: NS(extract_dup=lambda a, b: data, get_size=lambda: len(data)))


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
