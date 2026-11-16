"""Listening: the recognition worker and the audio capture it hears. Starts and pauses them,
switches the sound source, retries a capture or recognition that failed, and shows the worker's
events on the caption box. Uses the app's window, settings, transcript window and quit state; never
imports the windows (ui) or app.py."""
import logging
import os

from gi.repository import GLib

from .. import audio, engine, words

LOG = logging.getLogger('nowcaption')


class Listening:
    """Owns the recognition worker and the capture. All methods run on the GTK thread; worker and
    capture callbacks are passed here with GLib.idle_add. source_gen tags the capture (audio still
    on its way from an older source is dropped) and ui_gen the captions (captions from before the
    last Clear are dropped)."""

    def __init__(self, app):
        self.app = app
        self.worker = None
        self.ready = False
        self.paused = False  # Pause captions: nothing is captured until it is turned off
        self.source_gen = self.ui_gen = 0
        self.capture_failures = self.recognition_failures = 0
        self.retry_id = None
        self.recognition_retry_id = None
        self.recognition_retry_token = 0
        self.capture = audio.Capture(
            on_audio=lambda gen, pcm: self.worker.feed(gen, pcm),
            on_started=lambda gen: GLib.idle_add(self.capture_started, gen),
            on_ended=lambda gen, reason: GLib.idle_add(self.capture_ended, gen, reason))

    def start(self, history, word_list):
        """Start the recognition worker, keeping what is said in history (a file, or None while
        history is off) and fixing the words in word_list. The models load on the worker thread,
        so the window stays responsive meanwhile; capture starts once they are ready."""
        self.worker = engine.RecognitionWorker(lambda kind, value: GLib.idle_add(self.on_worker, kind, value))
        if history:
            self.worker.set_history(history)
        if word_list:
            self.worker.set_words(words.WordFixes(word_list))
        self.worker.start()

    def on_source(self, action, value):
        if self.app.closing or self.app.quitting.quit_requested:
            return
        self.cancel_recognition_retry(reset=True)
        action.set_state(value)
        self.app.settings.set('source', value.get_string())
        if self.ready:
            self.start_capture()
        elif self.worker and not self.app.closing:
            # replaces the load error until the model loads or fails again
            self.app.window.show_failure('model', 'Retrying the speech model…')
            self.worker.restart()

    def restart_listening(self):
        """Choose the same sound source again, which starts capture again, or loads the speech
        model again if it failed: More options' Restart, after a problem or a change of devices."""
        source = self.app.lookup_action('source')
        source.activate(source.get_state())

    def set_paused(self, paused):
        """Pause captions: capture stops, so nothing is heard or kept, and the words already
        heard become a finished line. Resuming listens again from the chosen source."""
        if paused == self.paused or self.app.closing or self.app.quitting.quit_requested:
            return
        self.paused = paused
        self.app.lookup_action('pause').set_state(GLib.Variant('b', paused))
        if paused:
            self.cancel_recognition_retry(reset=True)
            if self.retry_id:
                GLib.source_remove(self.retry_id)
                self.retry_id = None
            self.source_gen += 1  # audio still on its way from the old capture is dropped
            if self.worker:
                self.worker.set_source(self.source_gen)  # the words being spoken become a line
            self.capture.stop()
            if self.app.window:
                self.app.window.clear_failure('capture')
                self.app.window.show_failure('paused', 'Captions paused. Nothing is being listened to.')
        else:
            if self.app.window:
                self.app.window.clear_failure('paused')
            self.start_capture()

    def clear_transcript(self):
        if self.worker and not self.app.closing and not self.app.quitting.quit_requested:
            self.cancel_recognition_retry(reset=True)
            self.ui_gen += 1  # captions already on their way from before the clear are dropped
            self.worker.clear(self.ui_gen)
            self.app.window.show_text('')
            # Discard pre-Clear audio buffered in GStreamer as well as in recognition.
            if self.ready:
                self.start_capture()

    def stop(self):
        """The app is shutting down: stop retrying, and stop the capture and the worker without
        waiting for them (see threads_finished)."""
        self.cancel_recognition_retry()
        if self.retry_id:
            GLib.source_remove(self.retry_id)
            self.retry_id = None
        self.capture.stop()
        if self.worker:
            self.worker.stop()

    def threads_finished(self, timeout):
        """After the main loop, wait for the recognition worker and the capture to leave native
        code (never from the window's shutdown: a stalled model must not freeze it). Python 3.12
        and 3.13 end a daemon thread that is still in native code when the interpreter exits
        with pthread_exit, and sherpa-onnx's C++ code then aborts the process."""
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout)
        return self.capture.wait_stopped(timeout) and not (self.worker and self.worker.is_alive())

    def cancel_recognition_retry(self, reset=False):
        self.recognition_retry_token += 1
        if self.recognition_retry_id:
            GLib.source_remove(self.recognition_retry_id)
            self.recognition_retry_id = None
        if reset:
            self.recognition_failures = 0

    def retry_recognition(self, token, source_gen):
        if token != self.recognition_retry_token:
            return False
        self.recognition_retry_id = None
        self.recognition_retry_token += 1
        if self.worker and not self.app.closing and source_gen == self.source_gen:
            self.worker.restart(recover_only=True, source_gen=source_gen)
        return False

    # --- recognition worker events ----------------------------------------------------------

    def on_worker(self, kind, value):
        if self.app.closing:
            return False
        if kind == 'ready':
            self.ready = True
            self.app.window.clear_failure('model')
            self.start_capture()
        elif kind == 'caption':
            gen, text = value
            if gen == self.ui_gen:  # ignore captions from before the last Clear
                self.app.window.show_text(text)
                if self.app.transcript:
                    self.app.transcript.changed()
        elif kind == 'recognition-ok':
            if value == self.source_gen:
                self.cancel_recognition_retry(reset=True)
                self.app.window.clear_failure('recognition')
        elif kind == 'snapshot':
            fn, text = value
            fn(text)
        elif kind == 'snapshot-error':
            fn, message = value
            if fn:
                fn(message)
            else:
                self.app.window.show_status(f'Could not prepare the transcript: {message}', seconds=8)
        elif kind == 'warning':
            self.app.window.show_status(value, seconds=6)
        elif kind == 'overload':
            self.app.window.show_status(f'Falling behind: skipped {value:.1f} s of audio', seconds=4)
        elif kind == 'load-error':
            self.ready = False
            self.app.window.show_failure('model', f'{value} To try again, open More options and press Restart.')
        elif kind == 'error':
            source_gen, stream_gen, message = value
            if (source_gen != self.source_gen or
                    stream_gen != getattr(self.worker, 'stream_gen', stream_gen) or
                    not getattr(self.worker, 'broken', True)):
                return False  # an obsolete source/stream cannot spend the current retry budget
            self.cancel_recognition_retry()
            self.recognition_failures += 1
            if self.recognition_failures <= 3:
                delay = 2 ** self.recognition_failures
                self.app.window.show_failure('recognition', f'{message}. Restarting in {delay} s…')
                self.recognition_retry_id = GLib.timeout_add_seconds(
                    delay, self.retry_recognition, self.recognition_retry_token, self.source_gen)
            else:
                self.app.window.show_failure('recognition',
                                             f'{message}. To try again, open More options and press Restart.')
        return False

    # --- capture ----------------------------------------------------------------------------

    def start_capture(self):
        if not self.ready or self.app.closing or self.paused:
            return
        self.cancel_recognition_retry()
        if self.retry_id:
            GLib.source_remove(self.retry_id)
            self.retry_id = None
        mic = self.app.settings.get('source') == 'mic'
        self.source_gen += 1
        self.worker.set_source(self.source_gen)
        # NOWCAPTION_SOURCE names any PulseAudio/PipeWire source, overriding the menu choice
        device = os.environ.get('NOWCAPTION_SOURCE') or (audio.MICROPHONE if mic else audio.DESKTOP)
        try:
            self.capture.start(device, self.source_gen)
        except Exception as e:
            return self.capture_ended(self.source_gen, str(e))
        if not self.app.window.text:
            self.app.window.show_status('Listening to the microphone…' if mic else 'Listening to desktop audio…')

    def capture_started(self, gen):
        if gen == self.source_gen and self.capture_failures:
            self.capture_failures = 0
            self.app.window.clear_failure('capture')
            self.app.window.show_status('Audio is back.', seconds=3)
        return False

    def capture_ended(self, gen, reason):
        """Capture stopped on its own (device gone, sound server restarted…): retry with backoff."""
        if gen != self.source_gen or self.app.closing:
            return False
        delay = min(30, 2 ** self.capture_failures)
        self.capture_failures += 1
        LOG.warning('Audio capture stopped (%s); retrying in %d s', reason, delay)
        self.app.window.show_failure('capture', f'Audio capture stopped ({reason}). Retrying in {delay} s…')
        self.retry_id = GLib.timeout_add_seconds(delay, self.retry_capture)
        return False

    def retry_capture(self):
        self.retry_id = None
        self.start_capture()
        return False
