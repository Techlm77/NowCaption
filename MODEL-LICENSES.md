# Bundled models and libraries

NowCaption's own code is MIT licensed (see LICENSE). The Flatpak also contains:

| Component | Author | License | Source |
|---|---|---|---|
| Speech model: `nowcaption-fastconformer-en-80ms-cc-by-v2` (80 ms lookahead, int8 ONNX), NowCaption's fine-tune of `stt_en_fastconformer_hybrid_large_streaming_multi` | NVIDIA NeMo team; fine-tuned, exported to ONNX (adapted from the k2-fsa sherpa-onnx export script) and quantized by NowCaption. Training text from NVIDIA Parakeet TDT 0.6B v2 (CC-BY-4.0). Details in the model's README.md | CC-BY-4.0 | https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi |
| Punctuation and casing model (CNN-BiLSTM) | frankyoujian, Edge-Punct-Casing | Apache-2.0 | https://github.com/frankyoujian/Edge-Punct-Casing |
| sherpa-onnx | k2-fsa | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx |
| ONNX Runtime (inside the sherpa-onnx wheel) | Microsoft | MIT | https://github.com/microsoft/onnxruntime |
| NumPy | NumPy developers | BSD-3-Clause | https://numpy.org |
| gtk4-layer-shell | William Wold | MIT | https://github.com/wmww/gtk4-layer-shell |

## Speech used to fine-tune the speech model

The model's weights were trained on these recordings; none of them is bundled.

| Data | Author | License | Source |
|---|---|---|---|
| People's Speech, `clean` subset | MLCommons | CC-BY 2.0–4.0 | https://huggingface.co/datasets/MLCommons/peoples_speech |
| YODAS2, English videos with human subtitles | ESPnet / WAVLab | CC-BY-3.0 | https://huggingface.co/datasets/espnet/yodas2 |
| AMI Meeting Corpus | University of Edinburgh | CC-BY-4.0 | https://huggingface.co/datasets/edinburghcstr/ami |
| VoxPopuli, English | Meta AI | CC0-1.0 | https://huggingface.co/datasets/facebook/voxpopuli |
| LibriSpeech train-clean-360 and train-other-500 | Vassil Panayotov et al. | CC-BY-4.0 | https://www.openslr.org/12 |
| MUSAN (music, noise and babble mixed into the training audio) | David Snyder et al. | Creative Commons or public domain, per file | https://www.openslr.org/17 |
| RIRS_NOISES (room echo) | Tom Ko et al. | Apache-2.0 | https://www.openslr.org/28 |
