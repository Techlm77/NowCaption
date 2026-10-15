# Bundled models and libraries

NowCaption's own code is MIT licensed (see LICENSE). The Flatpak also contains:

| Component | Author | License | Source |
|---|---|---|---|
| Speech model: `stt_en_fastconformer_hybrid_large_streaming_multi` (80 ms lookahead, int8 ONNX) | NVIDIA NeMo team; ONNX export by NowCaption with `tools/export-model.py`, adapted from the k2-fsa sherpa-onnx export script | CC-BY-4.0 | https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi |
| Punctuation and casing model (CNN-BiLSTM) | frankyoujian, Edge-Punct-Casing | Apache-2.0 | https://github.com/frankyoujian/Edge-Punct-Casing |
| Background filter model: DPDFNet2 (streaming speech enhancement, used only when Background filter is on) | Ceva; ONNX packaging by k2-fsa | Apache-2.0 | https://github.com/ceva-ip/DPDFNet |
| sherpa-onnx | k2-fsa | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx |
| ONNX Runtime (inside the sherpa-onnx wheel) | Microsoft | MIT | https://github.com/microsoft/onnxruntime |
| NumPy | NumPy developers | BSD-3-Clause | https://numpy.org |
| gtk4-layer-shell | William Wold | MIT | https://github.com/wmww/gtk4-layer-shell |
| Translation speech model: Nemotron 3.5 ASR Streaming 0.6B (320 ms chunks, int8 ONNX; used only when Translate to English is on) | NVIDIA; ONNX packaging by k2-fsa | OpenMDW-1.1 (included as OpenMDW-1.1.txt and next to the model) | https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b |
| Translation model: NowCaption Translate v1 (33 languages to English, CTranslate2 int8; used only when Translate to English is on) | Techlm77; trained from scratch with `tools/translation-model` | CC-BY-4.0 (included next to the model) | https://github.com/Techlm77/NowCaption/releases/tag/translation-models-v2 |
| CTranslate2 | OpenNMT | MIT | https://github.com/OpenNMT/CTranslate2 |
| SentencePiece | Google | Apache-2.0 | https://github.com/google/sentencepiece |
| langid.py | Marco Lui | BSD-2-Clause | https://github.com/saffsd/langid.py |
