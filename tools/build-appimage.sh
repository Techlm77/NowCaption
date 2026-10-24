#!/bin/sh
# Build NowCaption as an AppImage: dist/NowCaption-<version>-<arch>.AppImage, plus the .zsync
# file that AppImage updaters read. Runs on Ubuntu 24.04 as root or with sudo: a GitHub
# runner, or from a checkout with
#   docker run --rm -v "$PWD:/src" -w /src ubuntu:24.04 tools/build-appimage.sh
# The AppImage bundles Python 3.12, GTK 4, libadwaita, GStreamer, gtk4-layer-shell, sherpa-onnx,
# NumPy and the models. Graphics drivers, fonts, Wayland, X11 and PipeWire come from the
# system, as the AppImage excludelist says. glibc comes from the system where it is 2.39 or
# newer; on older systems (Ubuntu 22.04, Debian 12, ...) AppRun starts Python with the glibc
# bundled in usr/lib/compat instead, which older system libraries also work with, together
# with the newer libstdc++ and HarfBuzz that the bundled libraries need there.
set -eu
cd "$(dirname "$0")/.."
SRC=$PWD
ARCH=$(uname -m)
LIB=/usr/lib/$ARCH-linux-gnu
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
WORK=${WORK:-/tmp/nowcaption-appimage}
APPDIR=$WORK/AppDir
OUT=$SRC/dist
SUDO=; [ "$(id -u)" = 0 ] || SUDO=sudo

# Pinned tools: appimagetool 1.9.1 and the type2 runtime 20251108 (needs no libfuse2).
case $ARCH in
    x86_64) TOOL_SHA=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
            RUNTIME_SHA=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d ;;
    aarch64) TOOL_SHA=f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158
             RUNTIME_SHA=00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444 ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
# Libraries every system provides itself: the AppImage excludelist
# (AppImageCommunity/pkg2appimage at 19e30b2).
EXCLUDE=' ld-linux.so.2 ld-linux-x86-64.so.2 ld-linux-aarch64.so.1 libanl.so.1 libBrokenLocale.so.1
 libcidn.so.1 libc.so.6 libdl.so.2 libm.so.6 libmvec.so.1 libnss_compat.so.2 libnss_dns.so.2
 libnss_files.so.2 libnss_hesiod.so.2 libnss_nisplus.so.2 libnss_nis.so.2 libpthread.so.0
 libresolv.so.2 librt.so.1 libthread_db.so.1 libutil.so.1 libstdc++.so.6 libGL.so.1 libEGL.so.1
 libGLdispatch.so.0 libGLX.so.0 libOpenGL.so.0 libdrm.so.2 libglapi.so.0 libgbm.so.1 libxcb.so.1
 libX11.so.6 libX11-xcb.so.1 libwayland-client.so.0 libasound.so.2 libfontconfig.so.1
 libfreetype.so.6 libharfbuzz.so.0 libcom_err.so.2 libexpat.so.1 libgcc_s.so.1 libgpg-error.so.0
 libICE.so.6 libSM.so.6 libusb-1.0.so.0 libuuid.so.1 libz.so.1 libjack.so.0 libpipewire-0.3.so.0
 libxcb-dri3.so.0 libxcb-dri2.so.0 libfribidi.so.0 libgmp.so.10 '
EXCLUDE=" $(echo $EXCLUDE) "  # one space between names, so each can be matched as " name "

echo "== Packages"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq --no-install-recommends \
    python3 python3-pip python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-girepository-2.0 \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-pulseaudio \
    librsvg2-common adwaita-icon-theme shared-mime-info dconf-gsettings-backend libglib2.0-bin \
    patchelf zsync curl ca-certificates file git bzip2 meson ninja-build gcc pkg-config \
    libgtk-4-dev libwayland-dev wayland-protocols libgirepository1.0-dev gobject-introspection \
    >/dev/null
rm -rf "$WORK"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib/girepository-1.0" "$APPDIR/usr/lib/gstreamer-1.0" \
    "$APPDIR/usr/lib/gdk-pixbuf-2.0/loaders" "$APPDIR/usr/lib/gio/modules" "$OUT"

echo "== gtk4-layer-shell 1.3.0 (not packaged by Ubuntu 24.04)"
git -c advice.detachedHead=false clone -q --depth 1 --branch v1.3.0 \
    https://github.com/wmww/gtk4-layer-shell.git "$WORK/gls"
[ "$(git -C "$WORK/gls" rev-parse HEAD)" = 1c963c51514581c41b9bdae08cdf69171265cdda ]
meson setup "$WORK/gls/build" "$WORK/gls" --prefix=/usr --libdir=lib -Dexamples=false \
    -Ddocs=false -Dtests=false -Dsmoke-tests=false -Dintrospection=true -Dvapi=false >/dev/null
meson compile -C "$WORK/gls/build" >/dev/null
DESTDIR="$WORK/gls-install" meson install -C "$WORK/gls/build" >/dev/null
cp -L "$WORK/gls-install/usr/lib/libgtk4-layer-shell.so.0" "$APPDIR/usr/lib/"
cp "$WORK/gls-install/usr/lib/girepository-1.0/Gtk4LayerShell-1.0.typelib" "$APPDIR/usr/lib/girepository-1.0/"

echo "== Python and NowCaption $VERSION"
cp -L /usr/bin/python3.12 "$APPDIR/usr/bin/"
ln -s python3.12 "$APPDIR/usr/bin/python3"
install -m644 appimage/nowcaption-appimage "$APPDIR/usr/bin/"
install -m755 appimage/gio-launch-desktop "$APPDIR/usr/bin/"  # see AppRun
mkdir -p "$APPDIR/usr/lib/python3.12"
tar cf - -C /usr/lib/python3.12 --exclude=__pycache__ --exclude=test --exclude=idlelib \
    --exclude=tkinter --exclude=turtledemo --exclude=lib2to3 --exclude=ensurepip \
    --exclude=pydoc_data --exclude='config-3.12-*' --exclude=sitecustomize.py \
    --exclude=sqlite3 --exclude=dbm --exclude=curses --exclude='_sqlite3.*' --exclude='_dbm.*' \
    --exclude='_gdbm.*' --exclude='_curses*' --exclude='readline.*' . |  # unused, with large libraries
    tar xf - -C "$APPDIR/usr/lib/python3.12"
DIST=$APPDIR/usr/lib/python3/dist-packages
mkdir -p "$DIST"
cp -a /usr/lib/python3/dist-packages/gi /usr/lib/python3/dist-packages/cairo "$DIST/"
python3 -m pip install -q --no-deps --only-binary=:all: --require-hashes --break-system-packages \
    --target "$DIST" -r tools/appimage-requirements.txt
cp -a src/nowcaption "$DIST/"
find "$APPDIR" -name __pycache__ -prune -exec rm -rf {} +
python3 -m compileall -q "$APPDIR/usr/lib/python3.12" "$DIST" >/dev/null || true

echo "== Models"
if [ ! -s models/nowcaption-fastconformer-en-80ms-cc-by-v2/encoder.int8.onnx ] ||
   [ ! -s models/sherpa-onnx-online-punct-en-2024-08-06/model.int8.onnx ]; then
    tools/fetch-models.sh
fi
M=$APPDIR/usr/share/nowcaption/models
for f in tokens.txt encoder.int8.onnx decoder.int8.onnx joiner.int8.onnx README.md; do
    install -Dm644 "models/nowcaption-fastconformer-en-80ms-cc-by-v2/$f" -t "$M/nowcaption-fastconformer-en-80ms-cc-by-v2"
done
for f in model.int8.onnx bpe.vocab README.md; do
    install -Dm644 "models/sherpa-onnx-online-punct-en-2024-08-06/$f" -t "$M/sherpa-onnx-online-punct-en-2024-08-06"
done

echo "== GTK, GStreamer and icon data"
python3 - "$LIB/girepository-1.0" "$APPDIR/usr/lib/girepository-1.0" <<'EOF'
# The typelibs NowCaption imports, and the ones they need.
import shutil, sys
import gi
gi.require_version('GIRepository', '2.0')
from gi.repository import GIRepository
GIRepository.Repository.prepend_search_path(sys.argv[2])  # gtk4-layer-shell's
repo = GIRepository.Repository.get_default()
for namespace, version in (('Gtk', '4.0'), ('Adw', '1'), ('Gst', '1.0'), ('GstApp', '1.0'),
                           ('Gtk4LayerShell', '1.0'), ('PangoCairo', '1.0')):
    repo.require(namespace, version, 0)
for name in repo.get_loaded_namespaces():
    path = repo.get_typelib_path(name)
    if not path.startswith(sys.argv[2]):
        shutil.copy(path, sys.argv[2])
EOF
for p in coreelements app audioconvert audioresample pulseaudio; do  # audio.PIPELINE's elements
    cp -L "$LIB/gstreamer-1.0/libgst$p.so" "$APPDIR/usr/lib/gstreamer-1.0/"
done
cp -L "$LIB/gio/modules/libdconfsettings.so" "$APPDIR/usr/lib/gio/modules/"
cp -L "$LIB/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader-svg.so" "$APPDIR/usr/lib/gdk-pixbuf-2.0/loaders/"
"$LIB/gdk-pixbuf-2.0/gdk-pixbuf-query-loaders" "$APPDIR"/usr/lib/gdk-pixbuf-2.0/loaders/*.so |
    sed "s|$APPDIR|@APPDIR@|g" > "$APPDIR/usr/lib/gdk-pixbuf-2.0/loaders.cache.in"
mkdir -p "$APPDIR/usr/share/glib-2.0/schemas" "$APPDIR/usr/share/icons/hicolor"
cp /usr/share/glib-2.0/schemas/org.gtk.gtk4.*.xml "$APPDIR/usr/share/glib-2.0/schemas/"
glib-compile-schemas "$APPDIR/usr/share/glib-2.0/schemas"
cp -a /usr/share/icons/Adwaita "$APPDIR/usr/share/icons/"
rm -rf "$APPDIR/usr/share/icons/Adwaita/cursors"  # the system's cursor theme is used
cp /usr/share/icons/hicolor/index.theme "$APPDIR/usr/share/icons/hicolor/"
mkdir -p "$APPDIR/usr/share/mime"  # for systems without shared-mime-info: SVG icons need it
find /usr/share/mime -maxdepth 1 -type f -exec cp {} "$APPDIR/usr/share/mime/" \;
install -Dm644 data/icons/io.github.Techlm77.NowCaption.svg -t "$APPDIR/usr/share/icons/hicolor/scalable/apps"
install -Dm644 data/io.github.Techlm77.NowCaption.desktop -t "$APPDIR/usr/share/applications"
install -Dm644 data/io.github.Techlm77.NowCaption.metainfo.xml -t "$APPDIR/usr/share/metainfo"
install -Dm644 LICENSE MODEL-LICENSES.md -t "$APPDIR/usr/share/licenses/io.github.Techlm77.NowCaption"

echo "== Libraries"
excluded() { case $EXCLUDE in *" $1 "*) return 0 ;; esac; return 1; }
wheels() { case $1 in */numpy*|*/sherpa_onnx*) return 0 ;; esac; return 1; }  # self-contained
# GObject introspection opens these by name, so ldd cannot see them.
gi_libs=$(python3 - "$APPDIR/usr/lib/girepository-1.0" <<'EOF'
import os, sys
import gi
gi.require_version('GIRepository', '2.0')
from gi.repository import GIRepository
GIRepository.Repository.prepend_search_path(sys.argv[1])
repo = GIRepository.Repository.get_default()
for name in sorted(os.listdir(sys.argv[1])):
    namespace, version = name[:-len('.typelib')].split('-', 1)
    repo.require(namespace, version, 0)
    print('\n'.join((repo.get_shared_library(namespace) or '').split(',')))
EOF
)
for soname in $gi_libs; do
    excluded "$soname" || [ -e "$APPDIR/usr/lib/$soname" ] || cp -L "$LIB/$soname" "$APPDIR/usr/lib/"
done
find "$APPDIR/usr" -type f | while read -r f; do
    wheels "$f" && continue
    file -b "$f" | grep -q '^ELF' || continue
    ldd "$f" | awk '/=> \//{print $1, $3}'
done | sort -u | while read -r soname path; do
    excluded "$soname" || [ -e "$APPDIR/usr/lib/$soname" ] || cp -L "$path" "$APPDIR/usr/lib/$soname"
done
find "$APPDIR/usr" -type f | while read -r f; do  # find bundled libraries through $ORIGIN
    wheels "$f" && continue
    file -b "$f" | grep -q '^ELF' || continue
    patchelf --set-rpath "\$ORIGIN/$(realpath --relative-to="$(dirname "$f")" "$APPDIR/usr/lib")" "$f"
done
missing=$(find "$APPDIR/usr" -type f -name '*.so*' | while read -r f; do
    wheels "$f" || LD_LIBRARY_PATH= ldd "$f" | grep 'not found' || true; done)
[ -z "$missing" ] || { echo "Unresolved libraries:" >&2; echo "$missing" >&2; exit 1; }

echo "== glibc and friends, for systems whose own are older"
# Kept out of usr/lib, so that the RUNPATHs above never pick them up: AppRun uses them only by
# starting Python through this glibc's loader, where the system's glibc is older than this one.
# There the system's libstdc++ and HarfBuzz are older than the bundled libraries need too
# (Pango wants HarfBuzz 7 or newer; Ubuntu 22.04 has 2.7).
C=$APPDIR/usr/lib/compat
mkdir -p "$C"
for soname in ld-linux-x86-64.so.2 ld-linux-aarch64.so.1 libc.so.6 libm.so.6 libmvec.so.1 libdl.so.2 \
              libpthread.so.0 librt.so.1 libresolv.so.2 libutil.so.1 libanl.so.1 libstdc++.so.6 libgcc_s.so.1 \
              libharfbuzz.so.0 libgraphite2.so.3; do
    [ ! -e "$LIB/$soname" ] || cp -L "$LIB/$soname" "$C/"
done
getconf GNU_LIBC_VERSION | cut -d' ' -f2 > "$C/GLIBC_VERSION"  # for example 2.39

echo "== Licenses"
# The copyright file of every Ubuntu package that bundled files came from goes in
# usr/share/licenses/<package>, and the packages' exact versions in ubuntu-packages.txt there.
L=$APPDIR/usr/share/licenses
install -Dm644 "$WORK/gls/LICENSE" -t "$L/gtk4-layer-shell"
find "$APPDIR/usr" -type f | while read -r f; do  # libraries, plugins, Python and typelibs, by name
    wheels "$f" && continue
    case $f in
        */libgtk4-layer-shell.so.0|*/Gtk4LayerShell-1.0.typelib) continue ;;  # built above
        *.typelib|*/schemas/*.xml) ;;
        *) file -b "$f" | grep -q '^ELF' || continue ;;
    esac
    case $f in "$APPDIR/usr/bin/"*) echo "/usr/bin/${f##*/}" ;; *) echo "*/${f##*/}" ;; esac
done > "$WORK/bundled"
# The data copied above, by where it came from.
printf '%s\n' /usr/lib/python3.12 /usr/lib/python3/dist-packages/gi /usr/lib/python3/dist-packages/cairo \
    /usr/share/icons/Adwaita /usr/share/icons/hicolor/index.theme /usr/share/mime >> "$WORK/bundled"
packages=$(xargs -d '\n' dpkg-query -S < "$WORK/bundled" 2> "$WORK/unowned" | grep -v '^diversion ' |
    sed 's/: .*//' | tr ',' '\n' | sed 's/^ *//; s/:.*//' | sort -u)
[ ! -s "$WORK/unowned" ] || { echo "Bundled files from no Ubuntu package:" >&2; cat "$WORK/unowned" >&2; exit 1; }
for p in $packages; do
    install -Dm644 "/usr/share/doc/$p/copyright" -t "$L/$p"
done
{
    echo "# The Ubuntu 24.04 packages bundled here, with their source packages, whose code is at"
    echo "# https://packages.ubuntu.com/noble/ (or: apt-get source <source>=<version>)."
    echo "# package version source source-version"
    dpkg-query -W -f='${Package} ${Version} ${source:Package} ${source:Version}\n' $packages
} > "$L/ubuntu-packages.txt"

echo "== AppImage"
install -m755 appimage/AppRun "$APPDIR/AppRun"
cp data/io.github.Techlm77.NowCaption.desktop data/icons/io.github.Techlm77.NowCaption.svg "$APPDIR/"
ln -s io.github.Techlm77.NowCaption.svg "$APPDIR/.DirIcon"
fetch() {  # url sha256 file
    curl -fsSL --retry 3 -o "$3" "$1"
    echo "$2  $3" | sha256sum -c - >/dev/null
}
fetch "https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-$ARCH.AppImage" "$TOOL_SHA" "$WORK/appimagetool"
fetch "https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-$ARCH" "$RUNTIME_SHA" "$WORK/runtime"
chmod +x "$WORK/appimagetool"
name=NowCaption-$VERSION-$ARCH.AppImage
(cd "$OUT" && rm -f "$name" "$name.zsync" &&
    ARCH=$ARCH APPIMAGE_EXTRACT_AND_RUN=1 "$WORK/appimagetool" --no-appstream --runtime-file "$WORK/runtime" \
        -u "gh-releases-zsync|Techlm77|NowCaption|latest|NowCaption-*-$ARCH.AppImage.zsync" \
        "$APPDIR" "$name" >/dev/null)
[ "$(id -u)" != 0 ] || chown --reference="$SRC" "$OUT" "$OUT/$name" "$OUT/$name.zsync"
ls -l "$OUT/$name" "$OUT/$name.zsync"
