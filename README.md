# NowCaption

Live captions for Linux. NowCaption shows what is being said in videos, calls,
streams and games, or into your microphone, in a small window that floats over
your desktop. It is made for people who are deaf or hard of hearing.

![NowCaption showing three lines of live captions](data/screenshots/captions.png)

- **Private.** Everything runs on your computer, offline. No account, and audio never leaves it.
- **Accurate.** Punctuation and capital letters included. 2.4% word errors on clear
  speech, a third of upstream Live Captions' 8.1% ([benchmark](benchmark/)).
- **Light.** Runs on the CPU as speech arrives. No GPU needed.
- **Translates to English** from 32 languages, also on your computer (optional).

## Install

```sh
flatpak install --user --from https://techlm77.github.io/NowCaption/nowcaption.flatpakref
```

Updates then arrive with `flatpak update` or your software centre. Works on x86_64
and ARM64. You can also download a `.flatpak` file from the
[latest release](https://github.com/Techlm77/NowCaption/releases/latest) and run
`flatpak install --user NowCaption-*.flatpak`.

To uninstall: `flatpak uninstall io.github.Techlm77.NowCaption`.

## Use

Captions start straight away from whatever your computer is playing. Drag them to
move, drag an edge to resize. **Right-click** for the menu:

- **Desktop audio** or **Microphone**
- **Background filter**: picks voices out of music and noise. Off by default, as it
  uses a little more CPU and slightly lowers accuracy on plain speech.
- **Translate to English**: English captions for speech in 32 languages, about a
  second behind the speaker. Best for Portuguese, German, French, Arabic, Bulgarian,
  Norwegian, Swedish and Ukrainian; roughest for Chinese, Japanese, Korean and the five
  marked "less reliable" ([quality and delay per language](benchmark/#per-language)).
  The language is detected for you, or pick it under **Spoken language**. Off by
  default, as it needs about 1.1 GB more memory.
- Text size, background, position
- Copy, save or clear the transcript

On KDE Plasma, Hyprland and Sway the captions float above all windows. On GNOME,
press Alt+Space on the window and choose **Always on Top**.

**No captions?** Make sure sound plays through your default output device. After
switching to headphones, choose **Desktop audio** again.

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

./tools/run-dev.sh                        # downloads the models on first run
./tools/fetch-models.sh --translation     # optional: models for Translate to English (1.3.0 or later)
./tools/build-flatpak.sh                  # or build the Flatpak
```

Tests: `PYTHONPATH=src .venv/bin/python tests/test_reliability.py` (fast, no models);
the other files in [`tests/`](tests/) say what they cover.

## Models

| Part | Model | License |
|---|---|---|
| Speech recognition | NVIDIA [FastConformer streaming](https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi), int8 ONNX, 80 ms lookahead | CC-BY-4.0 |
| Punctuation and casing | [Edge-Punct-Casing](https://github.com/frankyoujian/Edge-Punct-Casing) | Apache-2.0 |
| Background filter | [DPDFNet2](https://github.com/ceva-ip/DPDFNet) by Ceva | Apache-2.0 |
| Translation: speech | NVIDIA [Nemotron 3.5 ASR Streaming 0.6B](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) | OpenMDW-1.1 |
| Translation: text to English | NowCaption Translate v1 by Techlm77, trained from scratch ([how](tools/translation-model)), CTranslate2 int8 | CC-BY-4.0 |

Everything bundled is listed in [MODEL-LICENSES.md](MODEL-LICENSES.md). NowCaption's
own code is MIT licensed.
