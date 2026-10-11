"""Audio capture through GStreamer's PulseAudio source (works with PipeWire and in Flatpak)."""
import threading

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp  # noqa: E402,F401  (GstApp adds try_pull_sample)

DESKTOP = '@DEFAULT_MONITOR@'   # whatever the speakers/headphones are playing
MICROPHONE = '@DEFAULT_SOURCE@'


class Capture:
    """Delivers 100 ms blocks of 16 kHz mono PCM16 to on_audio(bytes) on a worker thread."""

    def __init__(self, on_audio, on_error=None):
        Gst.init(None)
        self.on_audio, self.on_error = on_audio, on_error
        self.pipeline, self.thread, self.running = None, None, False

    def start(self, device=DESKTOP):
        self.stop()
        self.pipeline = Gst.parse_launch(
            'pulsesrc name=src client-name=NowCaption buffer-time=100000 latency-time=20000 '
            '! audioconvert ! audioresample '
            '! audio/x-raw,format=S16LE,rate=16000,channels=1 '
            '! appsink name=sink sync=false max-buffers=50 drop=false emit-signals=false')
        self.pipeline.get_by_name('src').set_property('device', device)
        self.pipeline.get_by_name('src').set_property('stream-properties', Gst.Structure.new_from_string(
            'props,media.role=accessibility,application.icon_name=io.github.Techlm77.NowCaption'))
        self.running = True
        self.pipeline.set_state(Gst.State.PLAYING)
        self.thread = threading.Thread(target=self._pump, args=(self.pipeline,), daemon=True)
        self.thread.start()

    def _pump(self, pipeline):
        sink, bus, pending = pipeline.get_by_name('sink'), pipeline.get_bus(), b''
        block = 3200  # 100 ms of PCM16
        while self.running and pipeline is self.pipeline:
            msg = bus.pop_filtered(Gst.MessageType.ERROR)
            if msg:
                err, _ = msg.parse_error()
                if self.on_error:
                    self.on_error(err.message)
                return
            sample = sink.try_pull_sample(200 * Gst.MSECOND)
            if sample is None:
                continue
            buf = sample.get_buffer()
            pending += buf.extract_dup(0, buf.get_size())
            while len(pending) >= block:
                self.on_audio(pending[:block])
                pending = pending[block:]

    def stop(self):
        self.running = False
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        self.pipeline, self.thread = None, None
