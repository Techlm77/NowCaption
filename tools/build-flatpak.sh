#!/bin/sh
# Build and install the Flatpak for the current user from this checkout.
# Needs: flatpak, and once: flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//51 org.gnome.Platform//51
# If dist/ holds the model archive (before it is uploaded to a GitHub release), that file is used.
set -eu
cd "$(dirname "$0")/.."
manifest=io.github.Techlm77.NowCaption.yml
if ls dist/nowcaption-fastconformer-*.tar.bz2 >/dev/null 2>&1; then
    archive=$(ls dist/nowcaption-fastconformer-*.tar.bz2 | head -1)
    sed "s|url: https://github.com/Techlm77/NowCaption/releases/download/models-v[0-9]*/.*|path: $archive|" \
        $manifest > .local-manifest.yml
    manifest=.local-manifest.yml
fi
if command -v flatpak-builder >/dev/null; then
    builder=flatpak-builder
else  # the Flathub-packaged builder; give it the real user installation
    builder="flatpak run --env=FLATPAK_USER_DIR=$HOME/.local/share/flatpak --command=flatpak-builder org.flatpak.Builder"
fi
$builder --user --force-clean --repo=repo build-dir "$manifest"
# Install with this system's flatpak, not the builder's: an app the sandboxed builder installs
# gets a menu entry that runs its private /app/bin/flatpak, which does not exist outside it.
flatpak --user remote-add --if-not-exists --no-gpg-verify nowcaption-local "$PWD/repo"
flatpak --user install -y --noninteractive --reinstall nowcaption-local io.github.Techlm77.NowCaption
echo "Installed. Start NowCaption from the app menu, or: flatpak run io.github.Techlm77.NowCaption"
