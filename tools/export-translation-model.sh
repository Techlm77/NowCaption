#!/bin/sh
# Convert Meta's M2M100 418M (MIT) for NowCaption's optional translation to English:
# CTranslate2 int8 plus the model's own SentencePiece tokenizer, packed as
# dist/nowcaption-m2m100-418m-ct2-int8-v1.tar.bz2 for the translation-models-v1 release.
# Needs about 6 GB of disk and a virtual environment with:
#   pip install ctranslate2==4.8.3 transformers sentencepiece torch --extra-index-url https://download.pytorch.org/whl/cpu
set -eu
cd "$(dirname "$0")/.."
out=nowcaption-m2m100-418m-ct2-int8-v1
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
ct2-transformers-converter --model facebook/m2m100_418M --output_dir "$work/$out" --quantization int8
python3 - "$work/$out" <<'PY'
import shutil, sys
from huggingface_hub import hf_hub_download
shutil.copy(hf_hub_download('facebook/m2m100_418M', 'sentencepiece.bpe.model'), sys.argv[1])
PY
curl -fsSL https://raw.githubusercontent.com/facebookresearch/fairseq/main/LICENSE -o "$work/$out/LICENSE"
cp models/$out/README.md "$work/$out/" 2>/dev/null || cat > "$work/$out/README.md" <<'MD'
# M2M100 418M for NowCaption (CTranslate2, int8)

facebook/m2m100_418M (MIT, Copyright (c) Facebook, Inc. and its affiliates; see LICENSE),
converted with: ct2-transformers-converter --model facebook/m2m100_418M --quantization int8
MD
mkdir -p dist
tar -C "$work" -cf - "$out" | bzip2 -9 > "dist/$out.tar.bz2"
sha256sum "dist/$out.tar.bz2"
