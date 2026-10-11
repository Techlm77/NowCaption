#!/bin/sh
# Run NowCaption from a checkout without installing it.
# Needs: python3, python-gobject with cairo support, GTK >= 4.12, libadwaita >= 1.5,
# and GStreamer with gst-plugins-good (pulse). See README.md for package names.
# Checks the models and Python packages on every start and repairs them if a
# previous setup was interrupted or requirements.txt changed.
set -eu
cd "$(dirname "$0")/.."

need="models/nowcaption-fastconformer-en-80ms-cc-by-v1/tokens.txt
models/nowcaption-fastconformer-en-80ms-cc-by-v1/encoder.int8.onnx
models/nowcaption-fastconformer-en-80ms-cc-by-v1/decoder.int8.onnx
models/nowcaption-fastconformer-en-80ms-cc-by-v1/joiner.int8.onnx
models/sherpa-onnx-online-punct-en-2024-08-06/model.int8.onnx
models/sherpa-onnx-online-punct-en-2024-08-06/bpe.vocab"
for f in $need; do
    if [ ! -s "$f" ]; then echo "Model files missing ($f); fetching them."; ./tools/fetch-models.sh; break; fi
done

# .venv/.deps records the requirements it was installed from, written only after a
# successful install and import check.
want=$(sha256sum requirements.txt | cut -d' ' -f1)
if [ ! -x .venv/bin/python ] || [ "$(cat .venv/.deps 2>/dev/null)" != "$want" ]; then
    echo "Setting up the Python environment in .venv"
    rm -f .venv/.deps
    [ -x .venv/bin/python ] || python3 -m venv --system-site-packages .venv
    .venv/bin/pip install -q -r requirements.txt
    .venv/bin/python -c 'import sherpa_onnx, numpy, gi, cairo' || {
        echo "Python packages are installed but do not import; see the error above." >&2; exit 1; }
    echo "$want" > .venv/.deps
fi
PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" exec .venv/bin/python -m nowcaption "$@"
