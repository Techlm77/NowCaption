#!/bin/sh
# Test the installed Flatpak: the sandboxed engine captures a private null sink
# that the host plays the fixtures into. Nothing is played aloud.
set -eu
cd "$(dirname "$0")"
mod=$(pactl load-module module-null-sink sink_name=nowcaption_test)
trap 'pactl unload-module $mod' EXIT
out=$(mktemp)
flatpak run --filesystem="$PWD":ro --env=PYTHONPATH=/app/share/nowcaption --command=python3 \
    io.github.Techlm77.NowCaption "$PWD/capture_print.py" nowcaption_test.monitor 50 > "$out" 2>&1 &
until grep -q READY "$out" 2>/dev/null; do sleep 0.5; done; sleep 1
for f in fixtures/*.wav; do paplay --device=nowcaption_test "$f"; done
wait
grep -v READY "$out"
grep -q -i "turkish bath" "$out" && grep -q -i "christmas" "$out" && echo PASS || { echo FAIL; exit 1; }
