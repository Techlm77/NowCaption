"""The launch options (`nowcaption --transcript` and so on, as the desktop file's actions run them),
for this launch or a later one that GLib hands to the NowCaption already running. Each runs the
app action of the same name; never imports the windows (ui), and app.py only for annotations."""
from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import GLib

if TYPE_CHECKING:  # for annotations only
    from ..app import NowCaption

# the desktop file's actions run `nowcaption --transcript` and so on: see on_command_line
LAUNCH_OPTIONS = (('transcript', 'Open the transcript'), ('options', 'Open More options'),
                  ('toggle', 'Show or hide the captions'), ('pause', 'Pause or resume the captions'),
                  ('quit', 'Quit NowCaption if it is running'))
RUNNING_ONLY = ('toggle', 'pause')  # for a NowCaption already running; starting one just starts it


def add_launch_options(app: NowCaption) -> None:
    """Give the app's command line the launch options (LAUNCH_OPTIONS)."""
    for name, description in LAUNCH_OPTIONS:
        app.add_main_option(name, 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, description, None)


def on_command_line(app, command_line):
    """Each launch, this one or a later one that GLib hands to this running NowCaption:
    start the captions, or show them, then open what a launcher action asked for. --quit
    quits a running NowCaption and never starts one."""
    options = command_line.get_options_dict()
    chosen = [name for name, _ in LAUNCH_OPTIONS if options.contains(name)]
    if 'quit' in chosen:
        if app.window:
            app.activate_action('quit', None)
        return 0
    if not app.window:  # starting NowCaption shows its captions and listens
        chosen = [name for name in chosen if name not in RUNNING_ONLY]
    if not (app.window and chosen):  # an open window is not raised first, so the one asked for gets focus
        app.activate()
    elif app.hidden and 'toggle' not in chosen:
        app.toggle_captions()  # a window asked for from hidden captions brings them back too
    for name in chosen:
        app.activate_action(name, None)
    return 0
