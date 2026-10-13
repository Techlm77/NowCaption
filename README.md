# NowCaption

Live captions for Linux. NowCaption shows what is being said in videos, calls,
streams and games, or into your microphone, in a small caption window. It is made
for people who are deaf or hard of hearing.

![NowCaption showing three lines of live captions](data/screenshots/captions.png)

- **Private.** Speech is recognised on your computer. Audio never leaves it, it
  needs no account, and it works offline.
- **Accurate.** It writes punctuation and capital letters. Across 73 speakers of
  read English, it made under a third of the word errors of the April model in
  upstream Live Captions on clear speech (2.4% against 8.1%), and about two-fifths
  on harder recordings (7.0% against 17.9%) ([benchmark](benchmark/)).
- **Streaming.** Captions update as speech arrives. The model uses 80 ms lookahead;
  the delay you see also depends on audio capture, processor load and your desktop.
- **Runs on the CPU.** No dedicated GPU is needed. After digital silence has flushed
  the final words, recognition waits for more audio.

**English only for now.**

## Install

### With automatic updates (x86_64 and ARM64)

```sh
flatpak install --user --from https://techlm77.github.io/NowCaption/nowcaption.flatpakref
```

This adds the NowCaption repository, installs the app and pulls the GNOME runtime from
Flathub. New versions arrive with `flatpak update` or in your software centre. If you
installed a `.flatpak` file before, remove it first with
`flatpak uninstall io.github.Techlm77.NowCaption` (your settings are kept).

### Download a file

Download the `.flatpak` file for your computer from the
[latest release](https://github.com/Techlm77/NowCaption/releases/latest)
(`x86_64` for most PCs, `aarch64` for 64-bit ARM), then install it from the folder you
saved it in:

```sh
flatpak install --user NowCaption-*.flatpak
```

Flatpak downloads the GNOME runtime from Flathub automatically. From 1.0.7 on, the file
also adds the NowCaption repository, so later versions arrive with `flatpak update`.

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
  - Filter out music (off by default, see below)
  - Text size − / +, and background − / +
  - Reset position or size, or move to another screen if you have more than one
  - Copy, save or clear the transcript
  - Quit

### Filter out music

For songs, or speech over background music, turn on **Filter out music** in the
menu. A small voice-enhancement model then removes most of the music before
recognition. On a test song it lifted the words captioned from 49 to 134 out of
244. It is off by default because it uses about 0.1 of a processor core more, adds
about 20 ms of delay, and slightly lowers accuracy on plain speech (2.7% to 3.2%
word errors on clear speech). Sung lyrics remain much harder to caption than
speech, even with the filter ([benchmark](benchmark/#music-filter)).

If the filter fails, NowCaption turns it off, explains the error, and continues
captioning without it. Changing sources or turning the filter off drains its
remaining audio first. Clear deliberately discards buffered speech.

The transcript keeps everything said since NowCaption started, or since you
last chose **Clear**, including the words still being spoken. So that a long
session doesn't use more and more memory, finished lines go to a temporary file
in NowCaption's cache folder (`~/.var/app/io.github.Techlm77.NowCaption/cache/`
for the Flatpak). The file never has a name, so other programs can't open it, and
it is deleted when NowCaption quits, even if it crashes. **Clear** empties it. Like
any deleted file, its contents can remain on the disk until they are overwritten,
unless the disk is encrypted. Nothing else is written unless you choose
**Save transcript**.

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

Capture and recognition failures remain visible until that component actually
recovers. Buffered or replayed captions do not clear those warnings. Brief
notifications such as “Transcript copied” restore any unresolved warning when
they finish.

The captions show two lines unless you make the box taller (the screenshot shows
three, with the most solid background). New words appear at the end of the last
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
Every start checks all three models and required Python imports. It repairs
missing model files or broken packages, and recreates the Python environment
when its interpreter changes. Requirements and interpreter stamps are written
only after successful setup.

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
PYTHONPATH=src .venv/bin/python tests/test_fixes_112.py      # filter, recovery, warnings and setup fixes
PYTHONPATH=src .venv/bin/python tests/test_fixes_113.py      # the 1.1.3 memory-leak fix
PYTHONPATH=src .venv/bin/python tests/test_fixes_114.py      # the transcript file and bounded memory
PYTHONPATH=src .venv/bin/python tests/test_app.py           # application logic with real GTK, no display
xvfb-run -a env PYTHONPATH=src .venv/bin/python tests/test_window.py  # real GTK window and menu
PYTHONPATH=src .venv/bin/python tests/test_live.py          # from source (run tools/run-dev.sh once first)
PYTHONPATH=src .venv/bin/python tests/test_memory.py        # memory stays flat with the real models
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
| Music filter (optional) | [DPDFNet2](https://github.com/ceva-ip/DPDFNet) streaming speech enhancement by Ceva, packaged by k2-fsa | Apache-2.0 |

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

## License

NowCaption's code is MIT licensed. The bundled models and libraries keep their own
licenses, listed in [MODEL-LICENSES.md](MODEL-LICENSES.md).
