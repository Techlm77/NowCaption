#!/bin/sh
# Test the installed Flatpak: the sandboxed engine captures a private null sink
# that the host plays the fixtures into. Nothing is played aloud.
# Fails (instead of hanging) if the app does not start within STARTUP_SECONDS.
set -eu
cd "$(dirname "$0")"
STARTUP_SECONDS=${STARTUP_SECONDS:-90}
CAPTURE_SECONDS=${CAPTURE_SECONDS:-50}
sink=nowcaption_test_$$
out=$(mktemp)
pid=''
mod=$(pactl load-module module-null-sink sink_name="$sink")
cleanup() {
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null || true
    pactl unload-module "$mod" 2>/dev/null || true
    rm -f "$out"
}
trap cleanup EXIT INT TERM

flatpak run --filesystem="$PWD":ro --env=PYTHONPATH=/app/share/nowcaption --command=python3 \
    io.github.Techlm77.NowCaption "$PWD/capture_print.py" "$sink.monitor" "$CAPTURE_SECONDS" > "$out" 2>&1 &
pid=$!

waited=0
until grep -q '^READY$' "$out"; do
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "FAIL: the app exited before it was ready:"; cat "$out"; exit 1
    fi
    if [ "$waited" -ge $((STARTUP_SECONDS * 2)) ]; then
        echo "FAIL: not ready after ${STARTUP_SECONDS} s:"; cat "$out"; exit 1
    fi
    sleep 0.5; waited=$((waited + 1))
done
sleep 1
for f in fixtures/*.wav; do timeout 30 paplay --device="$sink" "$f"; done
waited=0
while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge $(((CAPTURE_SECONDS + 30) * 2)) ]; then
        echo "FAIL: the app did not finish:"; cat "$out"; exit 1
    fi
    sleep 0.5; waited=$((waited + 1))
done
wait "$pid" || { echo "FAIL: the app exited with an error:"; cat "$out"; pid=''; exit 1; }
pid=''
cat "$out"
python3 transcript_check.py "$out" fixtures/refs.json
echo PASS
