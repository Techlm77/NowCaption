#!/bin/sh
# Download, verify and unpack the speech and punctuation models into ./models
# (for running from a checkout). Safe to re-run: it repairs partial downloads and
# unpacks into a temporary folder first, so ./models never holds half a model.
# --translation also fetches the optional translation models (about 0.9 GB).
set -eu
translation=0
[ "${1:-}" = --translation ] && translation=1
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
# The optional music filter is a single ONNX file rather than an archive.
filter=.cache/dpdfnet2.onnx
if [ -f "$filter" ] && ! echo "ce35d6025fc71df0ef10d1540e1b7916837bbfe5f6896deb744508d2cad487a9  $filter" | sha256sum -c - >/dev/null 2>&1; then
    rm -f "$filter"
fi
[ -f "$filter" ] || curl -fL --retry 3 -o "$filter" \
    https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/dpdfnet2.onnx
echo "ce35d6025fc71df0ef10d1540e1b7916837bbfe5f6896deb744508d2cad487a9  $filter" | sha256sum -c -
mkdir -p models/dpdfnet2 && cp "$filter" models/dpdfnet2/
if [ $translation = 1 ]; then
    asr=sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-320ms-int8-2026-06-11
    fetch "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$asr.tar.bz2" \
          5f311142337a5c161e92d49f7a3009d8607d3836f39d610bff5307c74d1d2c53 "$asr"
    rm -rf "models/$asr/test_wavs"
    cp licenses/OpenMDW-1.1.txt "models/$asr/LICENSE"
    mt=nowcaption-m2m100-418m-ct2-int8-v1
    if [ -f "dist/$mt.tar.bz2" ]; then cp "dist/$mt.tar.bz2" .cache/; fi
    fetch "https://github.com/Techlm77/NowCaption/releases/download/translation-models-v1/$mt.tar.bz2" \
          7227c24af6a3666ee243e51562aa24153840f3bea5d1f41d413a1557b7ed1f2a "$mt"
fi
echo "Models are in ./models"
