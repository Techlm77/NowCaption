#!/bin/sh
# Pack a converted model as dist/nowcaption-translate-v1.tar.bz2 for the GitHub release.
# Usage: pack.sh <ct2-model-dir>
set -eu
here=$(cd "$(dirname "$0")" && pwd)
name=nowcaption-translate-v1
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/$name" dist
cp "$1"/model.bin "$1"/config.json "$1"/source_vocabulary.json "$1"/target_vocabulary.json \
   "$1"/source.spm "$1"/target.spm "$work/$name/"
cp "$here/model-README.md" "$work/$name/README.md"
cp "$here/LICENSE-CC-BY-4.0.txt" "$work/$name/LICENSE"
tar -C "$work" -cf - "$name" | bzip2 -9 > "dist/$name.tar.bz2"
sha256sum "dist/$name.tar.bz2"
