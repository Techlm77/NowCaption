#!/bin/sh
# Build the April ASR engine exactly as upstream Live Captions (abb128) ships it, for bench.py:
# abb128/april-asr at the commit Live Captions pins, ONNX Runtime 1.14.1 from its Flatpak
# manifest, and the April English model it downloads. Everything goes into ./april/.
# Then:  APRIL_LIB=april/build/libaprilasr.so python bench.py april \
#            --model april/april-english-dev-01110_en.april --name april-upstream --threads 1 ...
set -eu
cd "$(dirname "$0")"
APRIL_COMMIT=8b52f65dd3fbfa58064de85ab78237335b9de911  # subprojects/april-asr in abb128/LiveCaptions
ORT=onnxruntime-linux-x64-1.14.1.tgz
ORT_SHA=010585f534d822bf02d54c7cfc53b57aa3eb0cf34f8fb7503ee519ed18e25d29
MODEL=april-english-dev-01110_en.april
MODEL_SHA=77eb95d0fa4f7708a37e868c2265301ee6c42dcb25e63ca63eea3d9cbadbc1f3

mkdir -p april && cd april
[ -d april-asr ] || git clone -q https://github.com/abb128/april-asr.git
git -C april-asr checkout -q $APRIL_COMMIT
[ -f $ORT ] || curl -fsSLO https://github.com/microsoft/onnxruntime/releases/download/v1.14.1/$ORT
[ -f $MODEL ] || curl -fsSLO https://april.sapples.net/$MODEL
printf '%s  %s\n%s  %s\n' $ORT_SHA $ORT $MODEL_SHA $MODEL | sha256sum -c --quiet
rm -rf ort && mkdir ort && tar xzf $ORT -C ort --strip-components=1
# Name ONNX Runtime explicitly: a system onnxruntime found through pkg-config would otherwise win.
cmake -Wno-dev -Wno-deprecated -S april-asr -B build -DCMAKE_BUILD_TYPE=Release \
    -DONNXRuntime_LIBRARY="$PWD/ort/lib/libonnxruntime.so.1.14.1" \
    -DONNXRuntime_INCLUDE_DIR="$PWD/ort/include" -DCMAKE_BUILD_RPATH="$PWD/ort/lib" >/dev/null
cmake --build build -j"$(nproc)" >/dev/null
ldd build/libaprilasr.so | grep -q "$PWD/ort/lib/libonnxruntime.so.1.14.1" ||
    { echo "libaprilasr.so is not linked to ONNX Runtime 1.14.1" >&2; exit 1; }
echo "Built april/build/libaprilasr.so (april-asr ${APRIL_COMMIT%"${APRIL_COMMIT#???????}"}, ONNX Runtime 1.14.1)"
