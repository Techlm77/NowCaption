"""Audio capture through GStreamer's PulseAudio source (works with PipeWire and in Flatpak)."""
import logging
import threading

LOG = logging.getLogger(__name__)

DESKTOP = '@DEFAULT_MONITOR@'   # whatever the speakers/headphones are playing
MICROPHONE = '@DEFAULT_SOURCE@'
PIPELINE = ('pulsesrc name=src client-name=NowCaption buffer-time=100000 latency-time=20000 '
            '! audioconvert ! audioresample '
            '! audio/x-raw,format=S16LE,rate=16000,channels=1 '
            '! appsink name=sink sync=false max-buffers=50 drop=false emit-signals=false')
BLOCK = 3200  # 100 ms of 16 kHz PCM16


class Capture:
    """Runs one capture at a time. Each start() gets a generation number; its thread calls
      on_audio(gen, pcm_bytes)  for every 100 ms block,
      on_started(gen)           when the first audio arrives,
      on_ended(gen, reason)     if it stops without being asked to (device error, end of stream,
                                or an exception), after cleaning up.
    stop()/start() never wait for the old thread: it is told to stop, its pipeline is shut
    down, and anything it still delivers carries the old generation, which callers ignore.
    """

    def __init__(self, on_audio, on_started=None, on_ended=None, gst=None):
        self.on_audio, self.on_started, self.on_ended = on_audio, on_started, on_ended
        if gst is None:  # imported here so tests can run with a fake GStreamer and no PyGObject
            import gi
            gi.require_version('Gst', '1.0')
            gi.require_version('GstApp', '1.0')
            from gi.repository import Gst, GstApp  # noqa: F401  (GstApp adds try_pull_sample)
            gst = Gst
        self.gst = gst
        self.gst.init(None)
        self.current = None  # (gen, pipeline, stop_event, thread)

    def start(self, device, gen):
        self.stop()
        pipeline = self.gst.parse_launch(PIPELINE)
        src = pipeline.get_by_name('src')
        src.set_property('device', device)
        src.set_property('stream-properties', self.gst.Structure.new_from_string(
            'props,media.role=accessibility,application.icon_name=io.github.Techlm77.NowCaption'))
        stop = threading.Event()
        thread = threading.Thread(target=self._pump, args=(gen, pipeline, stop, device),
                                  name=f'nowcaption-capture-{gen}', daemon=True)
        if pipeline.set_state(self.gst.State.PLAYING) == self.gst.StateChangeReturn.FAILURE:
            pipeline.set_state(self.gst.State.NULL)
            raise RuntimeError('GStreamer could not start capturing from this sound source')
        self.current = (gen, pipeline, stop, thread)
        thread.start()

    def stop(self):
        if self.current:
            gen, pipeline, stop, thread = self.current
            self.current = None
            stop.set()
            pipeline.set_state(self.gst.State.NULL)  # also wakes a blocked try_pull_sample

    def _pump(self, gen, pipeline, stop, device):
        reason, started, pending = None, False, b''
        try:
            sink, bus = pipeline.get_by_name('sink'), pipeline.get_bus()
            src = pipeline.get_by_name('src')
            while not stop.is_set():
                msg = bus.pop_filtered(self.gst.MessageType.ERROR | self.gst.MessageType.EOS)
                if msg:
                    if msg.type == self.gst.MessageType.EOS:
                        reason = 'the audio stream ended'
                    else:
                        err, debug = msg.parse_error()
                        reason = err.message
                        LOG.warning('GStreamer error: %s (%s)', err.message, debug)
                    break
                sample = sink.try_pull_sample(200 * self.gst.MSECOND)
                if sample is None:  # a timeout, unless the sink has ended or was stopped
                    if sink.is_eos() and not stop.is_set():
                        reason = 'the audio stream ended'
                        break
                    continue
                if not started:
                    # PulseAudio silently falls back to the default device when a named one
                    # doesn't exist; never caption a source the user didn't choose.
                    actual = src.get_property('current-device')
                    if not device.startswith('@') and actual and actual != device:
                        reason = f'the sound source {device} was not found'
                        break
                    started = True
                    if self.on_started:
                        self.on_started(gen)
                buf = sample.get_buffer()
                pending += buf.extract_dup(0, buf.get_size())
                while len(pending) >= BLOCK:
                    self.on_audio(gen, pending[:BLOCK])
                    pending = pending[BLOCK:]
        except Exception as e:  # never let the capture thread die without saying so
            LOG.exception('Audio capture failed')
            reason = f'{type(e).__name__}: {e}'
        finally:
            pipeline.set_state(self.gst.State.NULL)
            if reason and not stop.is_set() and self.on_ended:
                self.on_ended(gen, reason)
