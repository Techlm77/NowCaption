# Licenses

NowCaption's own code is MIT licensed ([LICENSE](LICENSE)). The Flatpak and the AppImage also
contain the models and libraries below, each under its own license.

## Models

| Model | By | License |
|---|---|---|
| Speech recognition: [`nowcaption-fastconformer-en-80ms-cc-by-v2`](https://github.com/Techlm77/NowCaption/releases/tag/models-v2) | NowCaption, fine-tuned from NVIDIA's [FastConformer streaming](https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi) (NeMo team) | CC-BY-4.0 |
| Punctuation and capital letters: `sherpa-onnx-online-punct-en-2024-08-06` | [Edge-Punct-Casing](https://github.com/frankyoujian/Edge-Punct-Casing) by frankyoujian, packaged by k2-fsa | Apache-2.0 |

The speech model was trained with the text NVIDIA's Parakeet TDT 0.6B v2 (CC-BY-4.0) heard in
each recording, then exported to ONNX with an 80 ms lookahead and quantized to int8, with an
export script adapted from k2-fsa's sherpa-onnx. Its README.md, in the model archive, has the
details.

## Libraries

| Library | By | License |
|---|---|---|
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | k2-fsa | Apache-2.0 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime), inside sherpa-onnx | Microsoft | MIT |
| [NumPy](https://numpy.org) | NumPy developers | BSD-3-Clause |
| [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) | Sophie Winter | MIT |

The AppImage also contains Python 3.12, GTK 4, libadwaita, GStreamer, PyGObject, the Adwaita
icons and the libraries they need, taken from Ubuntu 24.04. Each keeps its own license, such as
Python's PSF license and the LGPL for GLib, GTK, libadwaita and GStreamer; their source is
available from [Ubuntu](https://packages.ubuntu.com/noble/). The Flatpak gets these from the
GNOME runtime instead.

Inside the AppImage (see it with `./NowCaption-*.AppImage --appimage-extract`),
`usr/share/licenses/<package>/copyright` has the copyright and license notice of each Ubuntu
package it contains files from, `usr/share/licenses/ubuntu-packages.txt` lists those packages
with their exact versions and source packages, and `usr/share/licenses/gtk4-layer-shell` has
gtk4-layer-shell's license.

## Speech the model was trained on

None of these recordings are bundled.

| Data | By | License |
|---|---|---|
| [People's Speech](https://huggingface.co/datasets/MLCommons/peoples_speech), `clean` subset | MLCommons | CC-BY 2.0–4.0 |
| [YODAS2](https://huggingface.co/datasets/espnet/yodas2), English videos with human subtitles | ESPnet / WAVLab | CC-BY-3.0 |
| [AMI Meeting Corpus](https://huggingface.co/datasets/edinburghcstr/ami) | University of Edinburgh | CC-BY-4.0 |
| [VoxPopuli](https://huggingface.co/datasets/facebook/voxpopuli), English | Meta AI | CC0-1.0 |
| [LibriSpeech](https://www.openslr.org/12) train-clean-360 and train-other-500 | Vassil Panayotov et al. | CC-BY-4.0 |
| [MUSAN](https://www.openslr.org/17): music, noise and babble mixed into the audio | David Snyder et al. | Creative Commons or public domain, per file |
| [RIRS_NOISES](https://www.openslr.org/28): room echo | Tom Ko et al. | Apache-2.0 |
