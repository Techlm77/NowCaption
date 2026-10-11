#!/bin/sh
# Run NowCaption from a checkout without installing it. Creates .venv on first use.
# Needs: python3, python-gobject, gtk4, libadwaita, gstreamer + gst-plugins-good (pulse).
set -eu
cd "$(dirname "$0")/.."
[ -d models ] || ./tools/fetch-models.sh
if [ ! -x .venv/bin/python ]; then
    python3 -m venv --system-site-packages .venv
    .venv/bin/pip install -q -r requirements.txt
fi
PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" exec .venv/bin/python -m nowcaption "$@"
