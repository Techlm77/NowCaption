# NowCaption

Live captions for Linux. NowCaption shows what is being said in videos, calls,
streams and games, or into your microphone, in a small window that floats above
your other windows. It is made for people who are deaf or hard of hearing.

![Live captions](data/screenshots/captions.png)

- **On your computer only.** Speech is recognised locally. Audio never leaves your
  computer, it needs no account, and it works offline.
- **Accurate.** English with punctuation and capital letters. It makes about half
  as many word errors as the April model used by Live Captions (see [benchmark](benchmark/)).
- **Fast.** Words appear about a third of a second after they are spoken.
- **Light.** It uses about a sixth of one CPU core while people talk, and nothing
  while it is quiet.

## Install

Once it is on Flathub:

```sh
flatpak install flathub io.github.Techlm77.NowCaption
```

Then open **NowCaption** from your app menu.

To build and install it from this repository:

```sh
flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//51 org.gnome.Platform//51
./tools/build-flatpak.sh
```

## Using it

- Captions start right away, from whatever your computer is playing.
- **Right-click** the captions to choose desktop audio or the microphone, change
  the text size or background, put them at the top or bottom of the screen, or
  copy, save or clear the transcript.
- **Drag** the captions to move them.
- Ctrl + plus / minus changes the text size, and Ctrl + Q quits. These work when
  the window has keyboard focus.

On KDE Plasma, Hyprland, Sway and other wlroots desktops, the captions stay above
all windows, including full-screen video. On GNOME, press Alt + Space on the
captions and choose **Always on Top**.

## Run from source

Install your distribution's PyGObject, GTK 4, libadwaita and GStreamer
(gst-plugins-good) packages. Optionally install gtk4-layer-shell. Then:

```sh
./tools/run-dev.sh       # downloads the models and creates .venv on first run
```

Tests:

```sh
PYTHONPATH=src .venv/bin/python tests/test_live.py   # GStreamer capture + engine, from source
./tests/test_flatpak.sh                              # the same, inside the installed Flatpak
```

Both tests play two LibriSpeech clips into a private virtual sink, so nothing is
heard. Setting `NOWCAPTION_SOURCE=<pulse source name>` makes the app caption any
source.

## How it works

`src/nowcaption/audio.py` captures 16 kHz mono audio with GStreamer's PulseAudio
source. That source works on PipeWire and inside Flatpak. `engine.py` streams the
audio into a sherpa-onnx transducer and restores punctuation and casing on each
line. Two decisions came from measurements:

- **The model's stream is never reset at pauses.** A reset drops the first words
  of the next phrase, and doing it at every pause made 50% more errors (4.0% to
  about 6%). Pauses only start a new caption line.
- **Digital silence is not processed.** The model's last words are flushed once,
  then it waits.

## Models

| | |
|---|---|
| Speech | NVIDIA `stt_en_fastconformer_hybrid_large_streaming_multi`, CC-BY-4.0, exported to ONNX int8 at 80 ms lookahead by `tools/export-model.py` |
| Punctuation and casing | Edge-Punct-Casing CNN-BiLSTM, Apache-2.0, packaged by k2-fsa |

See [MODEL-LICENSES.md](MODEL-LICENSES.md).

The exported speech model, `nowcaption-fastconformer-en-80ms-cc-by-v1.tar.bz2`,
is attached to the GitHub release `models-v1`, and the Flatpak manifest downloads
it from there. To rebuild it, run `tools/export-model.py --lookahead 1`. That
needs `nemo_toolkit[asr]`, `onnx` and `onnxruntime`. Then package the four files
and README as in that release.

## Publishing to Flathub

1. Push this repository to `github.com/Techlm77/NowCaption`.
2. Create the release `models-v1` and attach
   `dist/nowcaption-fastconformer-en-80ms-cc-by-v1.tar.bz2`. Check that its
   SHA-256 matches the manifest:
   `857639f81da8a52110d659f2d1a91bc5c25f0122f46fd3358091096b74a6ce80`.
3. Tag the app release (`git tag v1.0.0 && git push --tags`), then run
   `tools/make-flathub-manifest.sh v1.0.0`.
4. Check it with Flathub's linter:
   `flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest flathub/io.github.Techlm77.NowCaption.yml`
5. Follow https://docs.flathub.org/docs/for-app-authors/submission: fork
   `flathub/flathub`, branch from `new-pr`, add the generated manifest and open a
   pull request.

The `appid-url-not-reachable` and screenshot errors from the linter go away once
steps 1 and 2 are done. The Python dependencies are pinned upstream wheels for
x86_64 and aarch64. Building sherpa-onnx and ONNX Runtime from source inside
flatpak-builder would take much more work. If a Flathub reviewer asks for source
builds, that is the part to change.

## License

MIT for NowCaption's code. The bundled models and libraries keep their own
licenses, listed in [MODEL-LICENSES.md](MODEL-LICENSES.md).
