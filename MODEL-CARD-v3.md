# NowCaption speech model v3 (English, streaming, 80 ms lookahead)

NowCaption's own fine-tune of NVIDIA's `stt_en_fastconformer_hybrid_large_streaming_multi`
(transducer branch, attention context [70, 1] = 80 ms lookahead), as an int8 ONNX export
for sherpa-onnx. Same size, tokens and architecture as v2; trained further so it keeps working
after pauses in continuous speech and makes fewer errors on everyday speech and accents.

- Original model: https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi
- Original authors: NVIDIA NeMo team
- License: Creative Commons Attribution 4.0 International (CC-BY-4.0),
  https://creativecommons.org/licenses/by/4.0/
- Changes by NowCaption (Techlm77), 2026-10:
  - Continued from NowCaption's speech model v2 (itself fine-tuned from NVIDIA's model; see the v2
    archive's README.md). Training items join several clips with pauses between them, so the model
    keeps working after silences in a long stream, as the app hears speech.
  - Trained with short SpecAugment time masks and FastEmit (lambda 0.02).
  - The released weights blend two saves of that run in weight space: 75% of the save after 4 hours
    (25,117 steps, about 1,440 hours of speech seen) and 25% of the save after 8 hours (50,101 steps).
  - Exported to ONNX and dynamically quantized to int8 with per-channel weight scales (v2 used one
    scale per tensor), with an export script adapted from k2-fsa's sherpa-onnx
    (scripts/nemo/fast-conformer-hybrid-transducer-ctc), loading this checkpoint and selecting the
    80 ms context.

How it was trained and chosen:

- One NVIDIA GeForce RTX 5060 Ti (16 GB), NVIDIA NeMo 3.0.0, PyTorch 2.14.1 (CUDA 13.0).
- Run settings: learning rate 1e-4, batches of 240 s of audio, FastEmit 0.02, short time masks, a
  quarter of the items single clips and the rest several clips joined by pauses. Started from v2's
  weights. This was the third of three runs of the same recipe that changed one thing at a time; the
  pass and fail rules for each run were written down before its first score, and a held-back dev
  slice was scored every two hours. The run was stopped at 12.4 hours.
- The two saves blended (SHA-256): step0025117.nemo
  d230bb0ccb34a7a3cfdac1af53fa7a35ff4bbb4f2540c543f49afb8d1e84bb4e and step0050101.nemo
  d76a932cc4f9b12d10337cbf8170e25e4a6a1dbfe62a5f475286576c1405958f. For every floating-point
  tensor, in float32: three parts of the first to one of the second.
- The blend was chosen on the eight test sets below, then confirmed on the accent set and on 400
  other clips per set that played no part in the choice.
- Export: ONNX with onnx 1.23.2, quantized by onnxruntime 1.31.0's quantize_dynamic (QUInt8 weights,
  per channel). The export is byte-for-byte reproducible from the blended checkpoint.

Files (SHA-256):

    56aa4b512075376ff521b63b0707acf1776c9473ddf36e807b4f15e36e103eef  encoder.int8.onnx
    d11edd685c801ff1576fcba0ef99426f7b6db6fe937453104ac1a5b430e255df  decoder.int8.onnx
    03e3c27df10764e6a8a13d8dd7a8944a5bca0ebdd44c8a56e1b59a3f76f749ad  joiner.int8.onnx
    618dc110fc2213886b52e063ff42329bbdf37a266ca7705184090fa5f39f3131  tokens.txt

Training speech for v3 (3,348 hours prepared; only CC0 and CC-BY sources; speakers and utterances of
the test sets removed):

| Data | License |
|---|---|
| NVIDIA Granary, English YODAS part: YODAS2 videos (ESPnet / WAVLab) with NVIDIA's labels | audio CC-BY-3.0, labels CC-BY-4.0 |
| Common Voice 22.0, English (Mozilla) | CC0-1.0 |
| AMI Meeting Corpus (University of Edinburgh) | CC-BY-4.0 |
| LibriSpeech train-clean-100 and train-other-500 | CC-BY-4.0 |
| VoxPopuli English (Meta AI) | CC0-1.0 |

Augmentation: MUSAN (music, noise and speech under Creative Commons licenses or in the public
domain, per file) and RIRS_NOISES (OpenSLR SLR28, Apache-2.0).

Measured against v2 on a Ryzen 7 7800X3D, through NowCaption's own caption pipeline (one thread,
audio in 40 ms blocks, Whisper normalizer), on the ESB English test sets, 400 clips each (AMI,
Common Voice, Earnings-22, GigaSpeech, LibriSpeech clean and other, URGENT 2024, VoxPopuli):

| | v2 | v3 |
|---|---|---|
| Continuous use (one long stream, 2 s pauses between clips): word errors, average of the 8 sets | 17.31% | 13.31% |
| Continuous use: time until a word shows in its final form, median / 90% / 99% | 0.219 / 0.399 / 0.591 s | 0.210 / 0.406 / 0.619 s |
| Fresh stream for every clip: word errors | 13.51% | 13.18% |
| Fresh stream for every clip: time until a word shows, median / 90% / 99% | 0.317 / 0.528 / 0.736 s | 0.235 / 0.448 / 0.658 s |
| Accent test set (2,992 clips, 20 regions), continuous use | 17.24% | 13.78% |
| 400 other clips from each set, never used to choose this model: continuous use / fresh stream | 16.18% / 12.65% | 12.43% / 12.19% |
| CPU seconds per second of audio / peak memory of the whole caption process | 0.186 / 319 MB | 0.191 / 348 MB |

Targets set before training that v3 does not meet: at most 15% of one 7800X3D core (v3 uses 17.7%
in sustained real-time use, v2 17.2%); at most 12.5% word errors on the eight sets (v3: 13.31%
continuous, 13.18% fresh; 12.43% and 12.19% on the other clips); a median / 90% delay of at most
0.20 / 0.35 s in continuous use (0.210 / 0.406 s on the eight sets).

Known weaknesses: on a fresh stream, LibriSpeech test-clean is 0.38 points worse than v2 (0.28 on the
other clips, within chance there); short sentences right after a pause (Common Voice) show about
26 ms later than with v2; the slowest 1% of words in continuous use show about 28 ms later. See
benchmark/README.md in the NowCaption repository for the full results, ranges and methods.
