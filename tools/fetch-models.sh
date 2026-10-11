#!/bin/sh
# Download and verify the speech and punctuation models into ./models (for running from a checkout).
set -eu
cd "$(dirname "$0")/.."
mkdir -p models .cache
fetch() {  # url sha256
    f=".cache/$(basename "$1")"
    [ -f "$f" ] || curl -fL --retry 3 -o "$f.part" "$1" && { [ -f "$f" ] || mv "$f.part" "$f"; }
    echo "$2  $f" | sha256sum -c -
    tar xjf "$f" -C models
}
# Use a locally built archive (tools/export-model.py) if there is one, else the GitHub release.
if [ -f dist/nowcaption-fastconformer-en-80ms-cc-by-v1.tar.bz2 ]; then cp dist/nowcaption-fastconformer-en-80ms-cc-by-v1.tar.bz2 .cache/; fi
fetch https://github.com/Techlm77/NowCaption/releases/download/models-v1/nowcaption-fastconformer-en-80ms-cc-by-v1.tar.bz2 \
      857639f81da8a52110d659f2d1a91bc5c25f0122f46fd3358091096b74a6ce80
fetch https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-online-punct-en-2024-08-06.tar.bz2 \
      9f5e5a72c7d2829635bd074fce92b6bbd5b78da8a52e7ad8ed1be933f366b99d
echo "Models are in ./models"
