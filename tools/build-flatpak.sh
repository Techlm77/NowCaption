#!/bin/sh
# Build and install the Flatpak for the current user from this checkout.
# Needs: flatpak, and once: flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//51 org.gnome.Platform//51
# If dist/ holds the model archive (before it is uploaded to a GitHub release), that file is used.
set -eu
cd "$(dirname "$0")/.."
manifest=io.github.Techlm77.NowCaption.yml
if ls dist/nowcaption-fastconformer-*.tar.bz2 >/dev/null 2>&1; then
    archive=$(ls dist/nowcaption-fastconformer-*.tar.bz2 | head -1)
    sed "s|url: https://github.com/Techlm77/NowCaption/releases/download/models-v1/.*|path: $archive|" \
        $manifest > .local-manifest.yml
    manifest=.local-manifest.yml
fi
if command -v flatpak-builder >/dev/null; then
    builder=flatpak-builder
else  # the Flathub-packaged builder; give it the real user installation
    builder="flatpak run --env=FLATPAK_USER_DIR=$HOME/.local/share/flatpak --command=flatpak-builder org.flatpak.Builder"
fi
$builder --user --force-clean --repo=repo --install build-dir "$manifest"
echo "Installed. Start NowCaption from the app menu, or: flatpak run io.github.Techlm77.NowCaption"
