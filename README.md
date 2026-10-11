# NowCaption

Live captions for Linux. NowCaption shows what is being said in videos, calls,
streams and games, or into your microphone, in a small caption window. It is made
for people who are deaf or hard of hearing.

![NowCaption showing two lines of live captions](data/screenshots/captions.png)

- **Private.** Speech is recognised on your computer. Audio never leaves it, it
  needs no account, and it works offline.
- **Accurate.** It writes punctuation and capital letters. Across 73 speakers of
  read English, it made about a third of the word errors of Live Captions' April
  model on clear speech (2.4% against 7.8%), and fewer than half on harder
  recordings (7.0% against 16.0%) ([benchmark](benchmark/)).
- **Streaming.** Captions update as speech arrives. The model uses 80 ms lookahead;
  the delay you see also depends on audio capture, processor load and your desktop.
- **Runs on the CPU.** No dedicated GPU is needed. After digital silence has flushed
  the final words, recognition waits for more audio.

**English only for now.**

## Install

### Download (x86_64)

Download the `.flatpak` file from the
[latest release](https://github.com/Techlm77/NowCaption/releases/latest), then
install it from the folder you saved it in:

```sh
flatpak install --user NowCaption-*-x86_64.flatpak
```

Flatpak downloads the GNOME runtime from Flathub automatically. This file doesn't
update itself, so get new versions from the releases page. Installing a newer one
replaces the old version and keeps your settings.

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

- **Drag** the captions anywhere on the screen.
- **Drag an edge or corner** to make the box wider, narrower or taller. The
  height snaps to whole rows, so a taller box shows more lines of captions.
- **Right-click** them for a small menu:
  - Listen to desktop audio or the microphone
  - Text size − / +, and background − / +
  - Reset position or size, or move to another screen if you have more than one
  - Copy, save or clear the transcript
  - Quit

The transcript keeps everything said since NowCaption started, or since you
last chose **Clear**, including the words still being spoken. It's only kept in
memory and is lost when you quit, unless you save it. Nothing is written to disk
unless you choose **Save transcript**.

After choosing a destination, Quit and closing the window wait for that save
to finish. If the write fails, NowCaption stays open and shows the error so you
can choose another destination. Force-closing the process cannot preserve a
pending save.

Clear starts a fresh capture and recognition stream, so buffered speech from
before Clear is discarded. If recognition fails and restarts, the last unfinished
words already shown are kept in the transcript. If loading the model fails,
choose either sound source again to retry after correcting the reported problem.

Recognition errors are retried after 2, 4 and 8 seconds. If all three retries
fail, choose a sound source again to try manually. An actual successful decode
resets this count; replaying existing captions does not. Changing source, Clear
and shutdown cancel obsolete recognition timers.

If something goes wrong (for example the audio device disappears, or the sound
server restarts), the captions say what happened and NowCaption reconnects by
itself, retrying after 1, 2, 4, 8 and 16 seconds and then every 30 seconds for as
long as it takes. If the computer is too busy to keep up, NowCaption skips audio
rather than letting the captions fall more than about two seconds behind, and
says so.

The captions always show two lines. New words appear at the end of the second
line, and older text scrolls up out of view. Status messages use a separate,
small row while captions are visible, so copying, saving and warnings do not
cover speech. Long status messages have a tooltip with the full text.
Settings and position are remembered
between sessions.

### Staying above other windows

How the caption window behaves depends on your desktop:

| Desktop | Behaviour |
|---|---|
| KDE Plasma, Hyprland, Sway and other wlroots compositors (Wayland) | Captions float above all windows. Clicks outside the caption box go to the windows underneath. The captions never take keyboard focus, so use the menu. |
| GNOME (Wayland) | A normal window. To keep it on top, press Alt + Space while it is focused and choose **Always on Top**. Drag it anywhere. |
| X11 sessions | A normal window. Use your window manager's "keep above" option. |

In a normal window, Ctrl + plus and Ctrl + minus change the text size, Ctrl + S
saves the transcript, and Ctrl + Q quits. These shortcuts aren't available on the
overlay desktops above.

### If no captions appear

- Make sure the audio is playing through your **default** output device.
- After you switch output devices, for example to headphones, choose **Desktop
  audio** again from the menu. Capture then follows the new device.
- For the microphone, choose **Microphone** from the menu. It uses your default
  input device.

## Run from source

You need **GTK 4.12 or newer** and **libadwaita 1.5 or newer**. Debian 13,
Ubuntu 24.04, Fedora 40, Arch and newer have them; Debian 12 and Ubuntu 22.04 are
too old, so use the Flatpak there. NowCaption checks these versions when it starts
and explains if they are too old.

Install the system packages.

```sh
# Arch
sudo pacman -S python-gobject python-cairo gtk4 libadwaita gst-plugins-base gst-plugins-good gtk4-layer-shell
# Debian 13 / Ubuntu 24.04
sudo apt install python3-gi python3-gi-cairo python3-venv gir1.2-gtk-4.0 gir1.2-adw-1 \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-good gstreamer1.0-pulseaudio
# Fedora
sudo dnf install python3-gobject python3-cairo gtk4 libadwaita gstreamer1-plugins-base gstreamer1-plugins-good
```

gtk4-layer-shell is optional. It provides the floating overlay on KDE, Hyprland
and Sway. Only the Arch command has been tested.

Then run:

```sh
./tools/run-dev.sh
```

On the first run it downloads the models into `models/` and creates `.venv`.
Every start checks both and repairs them if an earlier setup was interrupted or
`requirements.txt` changed.

Problems are logged with details to standard error (the terminal, or the system
journal when started from the menu). `NOWCAPTION_LOG=info` logs more.

`NOWCAPTION_SOURCE=<source name>` makes the app caption any PulseAudio or PipeWire
source. Run `pactl list short sources` to see the names.

`NOWCAPTION_SCREENSHOT=<file.png>:<seconds>` saves GTK's own render of the
caption window after that many seconds. The store screenshot was made this way,
from LibriSpeech clips played into a virtual sink.

### Tests

```sh
PYTHONPATH=src .venv/bin/python tests/test_reliability.py   # worker, capture, transcript, setup scripts
PYTHONPATH=src .venv/bin/python tests/test_regressions.py   # the ten 1.0.3 fixes
PYTHONPATH=src .venv/bin/python tests/test_app.py           # application logic with real GTK, no display
xvfb-run -a env PYTHONPATH=src .venv/bin/python tests/test_window.py  # real GTK window and menu
PYTHONPATH=src .venv/bin/python tests/test_live.py          # from source (run tools/run-dev.sh once first)
./tests/test_flatpak.sh                                     # inside the installed Flatpak
```

`test_reliability.py` uses fake models and a fake GStreamer, needs only NumPy and
runs in about two seconds. It covers source switching, recognition, punctuation
and capture failures, end of stream, incomplete model files, Clear, Copy/Save
content, long sessions, and the setup and Flatpak-test scripts. `test_app.py`
covers quitting during loading, captions queued before a Clear, reconnect
backoff, monitor width and save errors. `test_regressions.py` covers model retry,
unfinished transcript recovery, buffered speech after Clear, bounded queues,
slow native capture setup/shutdown, resolution and scaling notifications, and
transcript/latency scoring. `test_window.py` constructs the real window and menu
under a display; Xvfb lets CI do this without a physical monitor. GitHub Actions
runs these on every push (`.github/workflows/tests.yml`) and fails if the required
GUI dependencies are missing. The live and Flatpak tests play two
LibriSpeech clips into a private virtual sink, so nothing plays out loud. They
need `pactl` and `paplay` (pipewire-pulse or pulseaudio-utils).

## How it works

`src/nowcaption/audio.py` captures 16 kHz mono audio with GStreamer's PulseAudio
source, which also works on PipeWire and inside Flatpak. `engine.py` streams that
audio into a sherpa-onnx transducer, then restores punctuation and casing.

One background thread, the recognition worker, owns the speech models. Audio,
source changes, Clear and transcript requests reach it through a single ordered
queue, so the models are never used from two threads at once. Each capture is
numbered, and audio from a capture that is still shutting down is ignored. The
queue contains at most twenty pending 100 ms audio blocks; older audio is dropped
at enqueue time if recognition stalls, while user commands are preserved. A
separate capture owner performs native pipeline setup and shutdown, so window
actions do not wait for GStreamer. Requested source changes coalesce until the
old pipeline has stopped. Errors are reported in the caption box and retried.

Two choices came from measurements:

- **Ordinary pauses keep the model's stream.** A reset loses the first words
  of the next phrase. Resetting at every pause more than doubled the word error
  rate on continuous speech, from 4.0% to 8.4%. Pauses only start a new caption
  line. Clear, source changes, recovery and dropped-audio discontinuities create
  a fresh stream deliberately.
- **Long digital silence becomes idle.** Silence is fed briefly to flush pending
  words, then the model waits for non-silent audio.

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
2. Tag and push, for example: `git tag v1.0.4 && git push --tags`.
3. Run `tools/make-flathub-manifest.sh v1.0.4`. It writes
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
