"""Launcher. Loads gtk4-layer-shell before GTK when it is available."""
import ctypes.util
import os
import sys
from pathlib import Path


def preload_layer_shell():
    """gtk4-layer-shell must load before libwayland-client, so re-exec with LD_PRELOAD once."""
    if os.environ.pop('NOWCAPTION_REEXEC', None):  # restarted below, or by the AppImage
        # Loaded now: programs started from NowCaption (a browser opened from About) get the
        # LD_PRELOAD it started with, without gtk4-layer-shell.
        host = os.environ.pop('NOWCAPTION_HOST_LD_PRELOAD', None)
        if host:
            os.environ['LD_PRELOAD'] = host
        elif host is not None:
            os.environ.pop('LD_PRELOAD', None)
        return
    if not os.environ.get('WAYLAND_DISPLAY'):
        return
    lib = ctypes.util.find_library('gtk4-layer-shell')
    for d in ('/app/lib', '/usr/lib', '/usr/local/lib', '/usr/lib64',
              '/usr/lib/x86_64-linux-gnu', '/usr/lib/aarch64-linux-gnu'):
        if not lib and Path(d, 'libgtk4-layer-shell.so.0').exists():
            lib = str(Path(d, 'libgtk4-layer-shell.so.0'))
    if lib and 'layer-shell' not in os.environ.get('LD_PRELOAD', ''):
        env = dict(os.environ, NOWCAPTION_REEXEC='1',
                   NOWCAPTION_HOST_LD_PRELOAD=os.environ.get('LD_PRELOAD', ''),
                   LD_PRELOAD=(lib + ' ' + os.environ.get('LD_PRELOAD', '')).strip())
        os.execve(sys.executable, [sys.executable, '-m', 'nowcaption'] + sys.argv[1:], env)


def main():
    preload_layer_shell()
    from .app import main as run
    return run()
