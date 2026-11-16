"""What NowCaption does while it runs, one collaborator per job; app.py creates them and wires them to
the Adw.Application, its windows and the desktop. Each holds the app and uses its window, settings
and the other collaborators through it. Nothing here imports app.py, and the windows (ui) never
import this package."""
from .history import History
from .listening import Listening
from .quitting import Quitting, quit_on_signals
from .saving import Saving

__all__ = ['History', 'Listening', 'Quitting', 'quit_on_signals', 'Saving']
