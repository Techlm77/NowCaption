# NowCaption

Live captions for Linux. NowCaption shows what is being said in videos, calls,
streams and games, or into your microphone, in a small window that floats over
your desktop. It is made for people who are deaf or hard of hearing.

![NowCaption showing three lines of live captions](data/screenshots/captions.png)

- **Private.** Everything runs on your computer, offline. No account, and audio never leaves it.
- **Accurate.** Punctuation and capital letters included. NowCaption's own speech model
  makes 2.5% word errors on clear speech, a third of upstream Live Captions' 8.1%, and
  fewer than the NVIDIA model it was trained from on meetings, calls and noisy speech
  ([benchmark](benchmark/)).
- **Light.** Runs on the CPU as speech arrives. No GPU needed.

## Install

```sh
flatpak install --user --from https://techlm77.github.io/NowCaption/nowcaption.flatpakref
```

Updates then arrive with `flatpak update` or your software centre. Works on x86_64
and ARM64. You can also download a `.flatpak` file from the
[latest release](https://github.com/Techlm77/NowCaption/releases/latest) and run
`flatpak install --user NowCaption-*.flatpak`.

To uninstall: `flatpak uninstall io.github.Techlm77.NowCaption`.

**AppImage:** download `NowCaption-*-x86_64.AppImage` (or `aarch64`) from the
[latest release](https://github.com/Techlm77/NowCaption/releases/latest), then run
`chmod +x NowCaption-*.AppImage` and `./NowCaption-*.AppImage`. It needs a system from
2024 or newer (glibc 2.39: Ubuntu 24.04, Debian 13, Fedora 40, Arch). Gear Lever or
AppImageUpdate can keep it up to date.

## Use

Captions start straight away from whatever your computer is playing. Drag them to
move, drag an edge to resize. **Right-click** for the menu:

- **Listen to:** desktop audio or your microphone
- **Appearance:** text size, background and text colour
- **Transcript:** open the transcript, keep history, or clear this session
- Reset the captions' position and size

**Transcript.** **Open transcript** shows everything captioned, in a window you can
scroll, select and copy from, and search with Ctrl+F. Earlier sessions are listed under
their date, and the current one updates as each line is finished. **Copy all** and
**Save** give the whole transcript; **Delete history** removes it.

**History.** NowCaption keeps each session's transcript for 7 days, then deletes it,
so it never fills up your disk. It stays on your computer, readable only by you, in
`~/.var/app/io.github.Techlm77.NowCaption/data/nowcaption/history` (Flatpak) or
`~/.local/share/nowcaption/history` (AppImage). Untick **Keep 7 days of history** in
the menu to stop keeping it. **Clear this session** also removes the current session
from the history.

**Text colour.** Captions are white by default. If another colour is easier for you to
read, choose yellow, cyan or lime under **Text colour**. The captions change as soon as you
click, so you can compare colours before closing the menu. NowCaption remembers your
choice, like your text size, background and position, every time it starts and after
updates.

On KDE Plasma, Hyprland and Sway the captions float above all windows. On GNOME,
press Alt+Space on the window and choose **Always on Top**.

**No captions?** Make sure sound plays through your default output device. After
switching to headphones, choose **Desktop audio** again.

**First word missing?** When speech is hard to make out, such as strong accents or
unclear recordings, the first word after a pause is the one most often missed or misheard,
because the speech model has nothing yet to go on. Clear speech is not affected.

## Run from source

Needs GTK 4.12+ and libadwaita 1.5+ (Debian 13, Ubuntu 24.04, Fedora 40, Arch).

```sh
# Arch
sudo pacman -S python-gobject python-cairo gtk4 libadwaita gst-plugins-base gst-plugins-good gtk4-layer-shell
# Debian / Ubuntu
sudo apt install python3-gi python3-gi-cairo python3-venv gir1.2-gtk-4.0 gir1.2-adw-1 \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-good gstreamer1.0-pulseaudio
# Fedora
sudo dnf install python3-gobject python3-cairo gtk4 libadwaita gstreamer1-plugins-base gstreamer1-plugins-good

./tools/run-dev.sh        # downloads the models on first run
./tools/build-flatpak.sh  # or build the Flatpak
```

Tests: `PYTHONPATH=src .venv/bin/python tests/test_reliability.py` (fast, no models);
the other files in [`tests/`](tests/) say what they cover.

## Models

| Part | Model | License |
|---|---|---|
| Speech recognition | NowCaption's own, fine-tuned from NVIDIA's [FastConformer streaming](https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi) | CC-BY-4.0 |
| Punctuation and capital letters | [Edge-Punct-Casing](https://github.com/frankyoujian/Edge-Punct-Casing) by frankyoujian | Apache-2.0 |

NowCaption's own code is MIT licensed. Everything else it ships with, and the speech its model
was trained on, is listed in [MODEL-LICENSES.md](MODEL-LICENSES.md).
