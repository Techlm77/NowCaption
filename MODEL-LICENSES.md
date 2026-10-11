# Bundled models and libraries

NowCaption's own code is MIT licensed (see LICENSE). The Flatpak also contains:

| Component | Author | License | Source |
|---|---|---|---|
| Speech model: `stt_en_fastconformer_hybrid_large_streaming_multi` (80 ms lookahead, int8 ONNX) | NVIDIA NeMo team; ONNX export by NowCaption with `tools/export-model.py`, adapted from the k2-fsa sherpa-onnx export script | CC-BY-4.0 | https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi |
| Punctuation and casing model (CNN-BiLSTM) | frankyoujian, Edge-Punct-Casing | Apache-2.0 | https://github.com/frankyoujian/Edge-Punct-Casing |
| sherpa-onnx | k2-fsa | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx |
| ONNX Runtime (inside the sherpa-onnx wheel) | Microsoft | MIT | https://github.com/microsoft/onnxruntime |
| NumPy | NumPy developers | BSD-3-Clause | https://numpy.org |
| gtk4-layer-shell | William Wold | MIT | https://github.com/wmww/gtk4-layer-shell |
