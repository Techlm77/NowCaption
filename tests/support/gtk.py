"""PyGObject with GTK 4 and libadwaita, and NowCaption's application module as nc, for the tests
that build the real application; no display is opened (support.windows adds one).

Without them nc is None and SKIP says why, for the tests' skipIf; with
NOWCAPTION_REQUIRE_GUI_TESTS=1 (tests/run.py --gui and CI set it) the import fails instead, so
a missing GTK can never pass as skipped tests.
"""
import os

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process

try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from nowcaption import app as nc
except (ImportError, ValueError) as e:
    if os.environ.get('NOWCAPTION_REQUIRE_GUI_TESTS') == '1':
        raise
    nc = None
    SKIP = f'PyGObject with GTK 4 and libadwaita is not available: {e}'
else:
    SKIP = ''



