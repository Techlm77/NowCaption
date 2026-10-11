#!/bin/sh
# Download, verify and unpack the speech and punctuation models into ./models
# (for running from a checkout). Safe to re-run: it repairs partial downloads and
# unpacks into a temporary folder first, so ./models never holds half a model.
set -eu
cd "$(dirname "$0")/.."
mkdir -p models .cache
fetch() {  # url sha256 folder
    f=".cache/$(basename "$1")"
    if [ -f "$f" ] && ! echo "$2  $f" | sha256sum -c - >/dev/null 2>&1; then
        echo "Cached $f is damaged; downloading it again."; rm -f "$f"
    fi
    if [ ! -f "$f" ]; then
        curl -fL --retry 3 -o "$f.part" "$1"
        mv "$f.part" "$f"
    fi
    echo "$2  $f" | sha256sum -c -
    tmp=$(mktemp -d models/.unpack.XXXXXX)
    tar xjf "$f" -C "$tmp"
    rm -rf "models/$3"
    mv "$tmp/$3" "models/$3"
    rm -rf "$tmp"
}
# Use a locally built archive (tools/export-model.py) if there is one, else the GitHub release.
asr=nowcaption-fastconformer-en-80ms-cc-by-v1
if [ -f "dist/$asr.tar.bz2" ]; then cp "dist/$asr.tar.bz2" .cache/; fi
fetch "https://github.com/Techlm77/NowCaption/releases/download/models-v1/$asr.tar.bz2" \
      857639f81da8a52110d659f2d1a91bc5c25f0122f46fd3358091096b74a6ce80 "$asr"
fetch https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-online-punct-en-2024-08-06.tar.bz2 \
      9f5e5a72c7d2829635bd074fce92b6bbd5b78da8a52e7ad8ed1be933f366b99d sherpa-onnx-online-punct-en-2024-08-06
echo "Models are in ./models"
