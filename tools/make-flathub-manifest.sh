#!/bin/sh
# Write flathub/io.github.Techlm77.NowCaption.yml for a Flathub submission/update:
# the same manifest, but the app source is this repo's git tag instead of the local folder.
# usage: tools/make-flathub-manifest.sh v1.0.0   (the tag must be pushed to GitHub)
set -eu
cd "$(dirname "$0")/.."
tag=${1:?usage: $0 <tag>}
commit=$(git rev-list -n 1 "$tag")
mkdir -p flathub
python3 - "$tag" "$commit" <<'PY'
import re, sys
tag, commit = sys.argv[1:]
s = open('io.github.Techlm77.NowCaption.yml').read()
src = re.search(r"      # Local builds use this checkout.*\Z", s, re.S).group(0)
s = s.replace(src, f"""      - type: git
        url: https://github.com/Techlm77/NowCaption.git
        tag: {tag}
        commit: {commit}
""")
open('flathub/io.github.Techlm77.NowCaption.yml', 'w').write(s)
PY
echo "Wrote flathub/io.github.Techlm77.NowCaption.yml ($tag = $commit)"
