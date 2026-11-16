#!/bin/sh
# Flatpak launcher. gtk4-layer-shell must be loaded before libwayland-client.
export PYTHONPATH=/app/share/nowcaption${PYTHONPATH:+:$PYTHONPATH}
# Flatpak sets WAYLAND_DISPLAY whenever the app may use Wayland. It is empty only when the user
# turned Flatpak's own Wayland socket off and shared the desktop's instead (the opt-in override
# for docking on Hyprland and Sway, see README.md): use that socket if it is the only one.
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    socket='' count=0
    for path in "$XDG_RUNTIME_DIR"/wayland-*; do
        case $path in *.lock) continue ;; esac
        [ -S "$path" ] && socket=${path##*/} && count=$((count + 1))
    done
    [ "$count" = 1 ] && export WAYLAND_DISPLAY="$socket"
# After that override is undone, Flatpak can't put its link to its socket back where the shared
# socket was mounted until the next login: connect to the socket the link would point to.
elif [ ! -S "${XDG_RUNTIME_DIR:-}/$WAYLAND_DISPLAY" ] && [ -S "/run/flatpak/$WAYLAND_DISPLAY" ]; then
    export WAYLAND_DISPLAY="/run/flatpak/$WAYLAND_DISPLAY"
fi
if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -e /app/lib/libgtk4-layer-shell.so.0 ]; then
    export LD_PRELOAD=/app/lib/libgtk4-layer-shell.so.0${LD_PRELOAD:+ $LD_PRELOAD}
fi
export NOWCAPTION_REEXEC=1
exec python3 -m nowcaption "$@"
