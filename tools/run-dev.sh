#!/bin/sh
# Run NowCaption from a checkout without installing it.
# Needs: python3, python-gobject with cairo support, GTK >= 4.12, libadwaita >= 1.5,
# and GStreamer with gst-plugins-good (pulse). See README.md for package names.
# Checks the models and Python packages on every start and repairs them if a
# previous setup was interrupted or requirements.txt changed.
set -eu
cd "$(dirname "$0")/.."

need="models/nowcaption-fastconformer-en-80ms-cc-by-v2/tokens.txt
models/nowcaption-fastconformer-en-80ms-cc-by-v2/encoder.int8.onnx
models/nowcaption-fastconformer-en-80ms-cc-by-v2/decoder.int8.onnx
models/nowcaption-fastconformer-en-80ms-cc-by-v2/joiner.int8.onnx
models/sherpa-onnx-online-punct-en-2024-08-06/model.int8.onnx
models/sherpa-onnx-online-punct-en-2024-08-06/bpe.vocab"
for f in $need; do
    if [ ! -s "$f" ]; then echo "Model files missing ($f); fetching them."; ./tools/fetch-models.sh; break; fi
done

# An existing executable can point at a new system Python after an upgrade. Keep
# its identity as well as the requirements hash, and check imports on every run.
identity='import os, sys; print(sys.implementation.cache_tag, os.path.realpath(getattr(sys, "_base_executable", sys.executable)), sys.version_info[:2])'
runtime=$(python3 -c "$identity")
venv_runtime=''
if [ -x .venv/bin/python ]; then
    venv_runtime=$(.venv/bin/python -c "$identity" 2>/dev/null || true)
fi
saved_runtime=$(cat .venv/.python 2>/dev/null || true)
if [ ! -x .venv/bin/python ] || [ "$venv_runtime" != "$runtime" ] || \
   { [ -n "$saved_runtime" ] && [ "$saved_runtime" != "$runtime" ]; }; then
    echo "Creating the Python environment in .venv for this interpreter"
    rm -rf .venv
    python3 -m venv --system-site-packages .venv
fi

want=$(sha256sum requirements.txt | cut -d' ' -f1)
imports='import sherpa_onnx, numpy, gi, cairo'
repair=''
if ! .venv/bin/python -c "$imports" >/dev/null 2>&1; then
    repair=--force-reinstall
fi
if [ "$(cat .venv/.deps 2>/dev/null || true)" != "$want" ] || [ -n "$repair" ]; then
    echo "Setting up the Python environment in .venv"
    rm -f .venv/.deps .venv/.python
    .venv/bin/python -m pip --version >/dev/null 2>&1 || .venv/bin/python -m ensurepip --upgrade
    .venv/bin/python -m pip install -q $repair -r requirements.txt
    .venv/bin/python -c "$imports" || {
        echo "Python packages are installed but do not import; see the error above." >&2; exit 1; }
    echo "$want" > .venv/.deps
fi
echo "$runtime" > .venv/.python
PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" exec .venv/bin/python -m nowcaption "$@"
