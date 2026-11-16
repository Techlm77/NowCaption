"""Quitting: Quit waits for the transcript saves already accepted (each holds the app), then fades the
caption box out and ends the app; SIGTERM and SIGHUP quit the same way. Uses the app's window and
its hold(), release() and Adw.Application.quit; never imports the windows (ui) or app.py."""
import signal

import gi

gi.require_version('Adw', '1')
from gi.repository import Adw, GLib

from ..style import FADE_OUT_MS


class Quitting:
    """Quit, the close button and signals all come here; so do the saves Quit must wait for."""

    def __init__(self, app):
        self.app = app
        self.pending_saves = 0
        self.quit_requested = False
        self.fading_out = False

    def begin_save(self):
        self.pending_saves += 1
        self.app.hold()

    def finish_save(self, success):
        self.pending_saves -= 1
        if not success:
            # A failed save needs a visible error and an opportunity to retry.
            self.quit_requested = False
        should_quit = self.quit_requested and not self.pending_saves
        self.app.release()
        if should_quit:
            self.quit()

    def request_quit(self):
        """Keep accepted saves alive until their snapshot and async write finish."""
        if self.pending_saves:
            self.quit_requested = True
            if self.app.window:
                self.app.window.show_status('Finishing transcript save before quitting…', sticky=True)
            return
        self.quit()

    def quit(self):
        """Fade the caption box out, then quit. A second request, or a fade that stalls
        because the screen stopped drawing, quits at once."""
        if self.app.window and not self.fading_out:
            self.fading_out = True
            GLib.timeout_add(FADE_OUT_MS + 350, self.quit)
            self.app.window.fade(self.app.window.box, None, 0.0, FADE_OUT_MS, self.quit)
            return False
        Adw.Application.quit(self.app)
        return False

    def on_window_close(self, window):
        self.request_quit()
        return True


def quit_on_signals(app):
    """Quit the way Quit does on SIGTERM and SIGHUP (kill, logging out, shutting down), so the
    newest lines still reach the history. A second signal quits without waiting for a save to
    finish. Returns the GLib sources."""
    try:
        gi.require_version('GLibUnix', '2.0')
        from gi.repository import GLibUnix
    except (ValueError, ImportError):  # GLib before 2.80
        GLibUnix = None
    # GLib 2.80's GLibUnix (Ubuntu 24.04, and so the AppImage) has only signal_add_full
    add = getattr(GLibUnix, 'signal_add', None) or GLib.unix_signal_add

    def on_signal(*_):
        if app.quitting.quit_requested or app.quitting.fading_out:
            app.quitting.quit()
        else:
            app.quitting.request_quit()
        return GLib.SOURCE_CONTINUE
    return [add(GLib.PRIORITY_DEFAULT, signum, on_signal) for signum in (signal.SIGTERM, signal.SIGHUP)]
