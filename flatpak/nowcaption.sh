#!/bin/sh
# Flatpak launcher. gtk4-layer-shell must be loaded before libwayland-client.
export PYTHONPATH=/app/share/nowcaption${PYTHONPATH:+:$PYTHONPATH}
if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -e /app/lib/libgtk4-layer-shell.so.0 ]; then
    export LD_PRELOAD=/app/lib/libgtk4-layer-shell.so.0${LD_PRELOAD:+ $LD_PRELOAD}
fi
export NOWCAPTION_REEXEC=1
exec python3 -m nowcaption "$@"
