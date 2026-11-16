"""What NowCaption does while it runs, one collaborator per job; app.py creates them and wires them to
the Adw.Application, its windows and the desktop. Each holds the app and uses its window, settings
and the other collaborators through it. Nothing here imports app.py but to name NowCaption in
annotations, nor the windows (ui), which never import this package either."""
from . import actions, commands
from .appearance import Appearance
from .history import History
from .listening import Listening
from .quitting import Quitting, quit_on_signals
from .saving import Saving

__all__ = ['actions', 'commands', 'Appearance', 'History', 'Listening', 'Quitting', 'quit_on_signals',
           'Saving']
