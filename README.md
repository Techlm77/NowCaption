# NowCaption

Live captions for Linux. NowCaption shows what is being said in videos, calls,
streams and games, or into your microphone, in a small caption window. It is made
for people who are deaf or hard of hearing.

![NowCaption showing two lines of live captions](data/screenshots/captions.png)

- **Private.** Speech is recognised on your computer. Audio never leaves it, it
  needs no account, and it works offline.
- **Accurate.** It writes punctuation and capital letters, and makes about half as
  many word errors as Live Captions' April model ([benchmark](benchmark/)).
- **Fast.** Words usually appear 0.35–0.45 seconds after they are spoken.
- **Light.** It uses about a sixth of one CPU core while people talk, and almost
  nothing in silence.

**English only for now.**

## Install

### Download (x86_64)

Download `NowCaption-1.0.0-x86_64.flatpak` from the
[latest release](https://github.com/Techlm77/NowCaption/releases/latest), then run:

```sh
flatpak install --user NowCaption-1.0.0-x86_64.flatpak
```

Flatpak downloads the GNOME runtime from Flathub automatically. This file doesn't
update itself, so get new versions from the releases page.

### From Flathub

NowCaption is not on Flathub yet. Once it is accepted:

```sh
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install flathub io.github.Techlm77.NowCaption
```

Then open **NowCaption** from your app menu.

### Build the Flatpak yourself

```sh
git clone https://github.com/Techlm77/NowCaption.git
cd NowCaption
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//51 org.gnome.Platform//51
./tools/build-flatpak.sh
```

The first build downloads the GNOME SDK, which is large.

### Uninstall

```sh
flatpak uninstall io.github.Techlm77.NowCaption
```

Settings are kept in `~/.var/app/io.github.Techlm77.NowCaption`. Delete that
folder too to remove them.

## Using it

Captions start straight away, from whatever your computer is playing.

- **Right-click** the captions for the menu:
  - Listen to desktop audio or the microphone
  - Make the text larger or smaller
  - Make the background more see-through or more solid
  - Copy, save or clear the transcript
  - Quit
- **Drag** the captions to move them.

Settings are remembered between sessions.

### Staying above other windows

How the caption window behaves depends on your desktop:

| Desktop | Behaviour |
|---|---|
| KDE Plasma, Hyprland, Sway and other wlroots compositors (Wayland) | Captions float above all windows. Dragging moves them up and down, and the menu can place them at the top or bottom of the screen. The overlay never takes keyboard focus, so use the menu rather than keyboard shortcuts. |
| GNOME (Wayland) | A normal window. To keep it on top, press Alt + Space while it is focused and choose **Always on Top**. Drag it anywhere. |
| X11 sessions | A normal window. Use your window manager's "keep above" option. |

In a normal window, Ctrl + plus and Ctrl + minus change the text size, Ctrl + S
saves the transcript, and Ctrl + Q quits.

### If no captions appear

- Make sure the audio is playing through your **default** output device.
- After you switch output devices, for example to headphones, choose **Desktop
  audio** again from the menu. Capture then follows the new device.
- For the microphone, choose **Microphone** from the menu. It uses your default
  input device.

## Run from source

Install the system packages.

```sh
# Arch
sudo pacman -S python-gobject gtk4 libadwaita gst-plugins-base gst-plugins-good gtk4-layer-shell
# Debian / Ubuntu
sudo apt install python3-gi python3-venv gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-good gstreamer1.0-pulseaudio
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita gstreamer1-plugins-base gstreamer1-plugins-good
```

gtk4-layer-shell is optional. It provides the floating overlay on KDE, Hyprland
and Sway. Only the Arch command has been tested.

Then run:

```sh
./tools/run-dev.sh
```

On the first run it downloads the models into `models/` and creates `.venv`.

`NOWCAPTION_SOURCE=<source name>` makes the app caption any PulseAudio or PipeWire
source. Run `pactl list short sources` to see the names.

`NOWCAPTION_SCREENSHOT=<file.png>:<seconds>` saves GTK's own render of the
caption window after that many seconds. The store screenshot was made this way,
from LibriSpeech clips played into a virtual sink.

### Tests

```sh
PYTHONPATH=src .venv/bin/python tests/test_live.py   # from source (run tools/run-dev.sh once first)
./tests/test_flatpak.sh                              # inside the installed Flatpak
```

Both tests play two LibriSpeech clips into a private virtual sink, so nothing
plays out loud. They need `pactl` and `paplay` (pipewire-pulse or pulseaudio-utils).

## How it works

`src/nowcaption/audio.py` captures 16 kHz mono audio with GStreamer's PulseAudio
source, which also works on PipeWire and inside Flatpak. `engine.py` streams that
audio into a sherpa-onnx transducer, then restores punctuation and casing on each
line. Two choices came from measurements:

- **The model's stream is never reset at pauses.** A reset loses the first words
  of the next phrase. Resetting at every pause more than doubled the word error
  rate on continuous speech, from 4.0% to 8.4%. Pauses only start a new caption
  line.
- **Digital silence is not processed.** When the audio goes completely silent,
  the last words are flushed once and the model waits.

## Models

| Part | Model | License |
|---|---|---|
| Speech recognition | NVIDIA [`stt_en_fastconformer_hybrid_large_streaming_multi`](https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi), exported to ONNX int8 with 80 ms lookahead | CC-BY-4.0 |
| Punctuation and casing | [Edge-Punct-Casing](https://github.com/frankyoujian/Edge-Punct-Casing) CNN-BiLSTM, packaged by k2-fsa | Apache-2.0 |

[MODEL-LICENSES.md](MODEL-LICENSES.md) lists everything bundled in the Flatpak.

The exported speech model is attached to the
[`models-v1` release](https://github.com/Techlm77/NowCaption/releases/tag/models-v1),
and the Flatpak build downloads it from there. To recreate it:

```sh
python3.12 -m venv .venv-export     # NeMo supports Python 3.10–3.12
.venv-export/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv-export/bin/pip install "nemo_toolkit[asr]" onnx onnxruntime
mkdir -p export && cd export
../.venv-export/bin/python ../tools/export-model.py --lookahead 1
```

This writes `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx` and
`tokens.txt`. The release archive holds those four files and a README in a folder
named `nowcaption-fastconformer-en-80ms-cc-by-v1`.

## Releasing a new version (maintainers)

1. Add a `<release>` entry to `data/io.github.Techlm77.NowCaption.metainfo.xml`
   and set `__version__` in `src/nowcaption/__init__.py`.
2. Tag and push: `git tag v1.0.0 && git push --tags`.
3. Run `tools/make-flathub-manifest.sh v1.0.0`. It writes
   `flathub/io.github.Techlm77.NowCaption.yml` with the source pinned to that tag.
4. Check it:
   `flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest flathub/io.github.Techlm77.NowCaption.yml`
5. For the first release, follow the
   [Flathub submission guide](https://docs.flathub.org/docs/for-app-authors/submission).
   Later releases go to the app's own repository on Flathub.

The Python dependencies are pinned upstream wheels for x86_64 and aarch64, because
building sherpa-onnx and ONNX Runtime from source in flatpak-builder takes much
more work. If Flathub reviewers ask for source builds, that is the part to change.

## License

NowCaption's code is MIT licensed. The bundled models and libraries keep their own
licenses, listed in [MODEL-LICENSES.md](MODEL-LICENSES.md).
