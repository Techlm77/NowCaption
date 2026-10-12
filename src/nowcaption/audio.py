"""Audio capture through GStreamer's PulseAudio source (also works with PipeWire)."""
import logging
import threading

LOG = logging.getLogger(__name__)

DESKTOP = '@DEFAULT_MONITOR@'
MICROPHONE = '@DEFAULT_SOURCE@'
PIPELINE = ('pulsesrc name=src client-name=NowCaption buffer-time=100000 latency-time=20000 '
            '! audioconvert ! audioresample '
            '! audio/x-raw,format=S16LE,rate=16000,channels=1 '
            '! appsink name=sink sync=false max-buffers=50 drop=false emit-signals=false')
BLOCK = 1280  # 40 ms of 16 kHz PCM16: smaller blocks reach the model sooner (benchmark/README.md)


class Capture:
    """One background owner creates, runs and shuts down every native pipeline.

    start(device, gen) and stop() only change the desired state; neither waits for
    GStreamer. Rapid changes coalesce to the newest request. A replacement cannot
    start until the old pipeline has shut down. Callbacks carry the capture generation:
    on_audio(gen, pcm), on_started(gen), on_ended(gen, reason). Requested stops are quiet.
    """

    def __init__(self, on_audio, on_started=None, on_ended=None, gst=None):
        self.on_audio, self.on_started, self.on_ended = on_audio, on_started, on_ended
        if gst is None:
            import gi
            gi.require_version('Gst', '1.0')
            gi.require_version('GstApp', '1.0')
            from gi.repository import Gst, GstApp  # noqa: F401
            gst = Gst
        self.gst = gst
        self.gst.init(None)
        self.current = None  # (gen, pipeline, stop_event, owner_thread), for diagnostics
        self._lock = threading.Lock()
        self._desired = self._stop = self._thread = None
        self._stranded = None  # retain a pipeline whose shutdown failed; retry before replacing it

    def start(self, device, gen):
        with self._lock:
            if self._stop:
                self._stop.set()
            self._desired = (device, gen)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='nowcaption-capture', daemon=True)
                self._thread.start()

    def stop(self):
        with self._lock:
            self._desired = None
            if self._stop:
                self._stop.set()

    def wait_stopped(self, timeout=2):
        """For CLI tests only; window actions never wait for native capture shutdown."""
        with self._lock:
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def _run(self):
        while True:
            with self._lock:
                if self._desired is None:
                    self._thread = None
                    return
                device, gen = self._desired
                self._desired = None
                stop = self._stop = threading.Event()
            pipeline, reason = None, None
            try:
                if self._stranded is not None:
                    self._shutdown(self._stranded)
                    self._stranded = None
                pipeline = self.gst.parse_launch(PIPELINE)
                src = pipeline.get_by_name('src')
                src.set_property('device', device)
                src.set_property('stream-properties', self.gst.Structure.new_from_string(
                    'props,media.role=accessibility,application.icon_name=io.github.Techlm77.NowCaption'))
                with self._lock:
                    self.current = (gen, pipeline, stop, self._thread)
                if not stop.is_set():
                    if pipeline.set_state(self.gst.State.PLAYING) == self.gst.StateChangeReturn.FAILURE:
                        raise RuntimeError('GStreamer could not start capturing from this sound source')
                    reason = self._pump(gen, pipeline, stop, device)
            except Exception as e:
                LOG.exception('Audio capture failed')
                reason = f'{type(e).__name__}: {e}'
            finally:
                if pipeline is not None:
                    try:
                        self._shutdown(pipeline)
                    except Exception as e:
                        LOG.exception('Audio shutdown failed')
                        reason = f'Audio shutdown failed: {e}'
                        self._stranded = pipeline
                with self._lock:
                    self.current, self._stop = None, None
            if reason and not stop.is_set() and self.on_ended:
                try:
                    self.on_ended(gen, reason)
                except Exception:
                    LOG.exception('Audio error callback failed')

    def _shutdown(self, pipeline):
        if pipeline.set_state(self.gst.State.NULL) == self.gst.StateChangeReturn.FAILURE:
            raise RuntimeError('GStreamer could not shut down the previous capture')

    def _pump(self, gen, pipeline, stop, device):
        started, pending = False, b''
        sink, bus = pipeline.get_by_name('sink'), pipeline.get_bus()
        src = pipeline.get_by_name('src')
        while not stop.is_set():
            msg = bus.pop_filtered(self.gst.MessageType.ERROR | self.gst.MessageType.EOS)
            if msg:
                if msg.type == self.gst.MessageType.EOS:
                    return 'the audio stream ended'
                err, debug = msg.parse_error()
                LOG.warning('GStreamer error: %s (%s)', err.message, debug)
                return err.message
            sample = sink.try_pull_sample(200 * self.gst.MSECOND)
            if sample is None:
                if sink.is_eos() and not stop.is_set():
                    return 'the audio stream ended'
                continue
            if stop.is_set():
                break
            if not started:
                actual = src.get_property('current-device')
                if not device.startswith('@') and actual and actual != device:
                    return f'the sound source {device} was not found'
                started = True
                if self.on_started:
                    self.on_started(gen)
            buf = sample.get_buffer()
            pending += buf.extract_dup(0, buf.get_size())
            while len(pending) >= BLOCK and not stop.is_set():
                self.on_audio(gen, pending[:BLOCK])
                pending = pending[BLOCK:]
        return None
