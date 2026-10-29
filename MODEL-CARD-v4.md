# NowCaption speech model v4 (English, streaming, 80 ms lookahead)

NowCaption's own fine-tune of NVIDIA's `stt_en_fastconformer_hybrid_large_streaming_multi`
(transducer branch, attention context [70, 1] = 80 ms lookahead), as an int8 ONNX export
for sherpa-onnx. Same size, tokens and architecture as v3; trained further on transcripts that
keep the words at the start and end of each clip, and exported with an encoder that skips work
repeated at every step, so it needs less than half the CPU time of v3.

- Original model: https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi
- Original authors: NVIDIA NeMo team
- License: Creative Commons Attribution 4.0 International (CC-BY-4.0),
  https://creativecommons.org/licenses/by/4.0/
- Transcripts of the training speech were partly written by NVIDIA's `parakeet-tdt-0.6b-v2`
  (https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2, NVIDIA NeMo team, CC-BY-4.0). None of its
  weights are in this model.
- Changes by NowCaption (Techlm77), 2026-10:
  - Continued from NowCaption's speech model v3 (see the v3 archive's README.md).
  - New transcripts: parakeet-tdt-0.6b-v2 transcribed all 1,387,393 training clips. Granary's machine
    labels often leave out words at a clip's edges, which teaches a model to drop them; for those clips
    its transcript replaced the label wherever it was plausible for the clip's length. Human-labelled
    clips kept their own text, and were left out where their label and its transcript disagreed by more
    than 60% (word error rate).
  - Stage 1, 8 hours from v3: learning rate 5e-5; 60% of batches at 80 ms lookahead and 40% at none;
    FastEmit 0.02; short time masks. The run stopped at 4.0 hours on a GPU fault and resumed from its
    last save; the second 4 hours added a delay penalty of 0.002 per 80 ms frame.
  - Stage 2, 8 hours from stage 1's final weights: learning rate 3e-5, 80 ms lookahead only, FastEmit
    0.02, delay penalty 0.002, and a new order of the training items. The released weights are its
    save after 7.0 hours (43,775 steps, about 2,500 hours of speech seen).
  - Exported to ONNX with the same maths in a faster form: the attention cache keeps each layer's keys
    and values instead of re-projecting 70 frames at every step (so the cache's last dimension is 1,024,
    which sherpa-onnx reads from the model's metadata), the position-encoding projection is worked out
    once at export, and pointwise convolutions run as matrix multiplies. Dynamically quantized to int8
    with per-channel weight scales, except the depthwise convolutions and the position table, which stay
    float32. At full precision, its outputs match the standard export's to about 2 parts per million.

How it was trained and chosen:

- One NVIDIA GeForce RTX 5060 Ti (16 GB), NVIDIA NeMo 3.0.0, PyTorch 2.14.1 (CUDA 13.0).
- Batches of 240 s of audio, a quarter of the items single clips and the rest several clips joined by
  pauses, as for v3. Training clips from the speakers, videos and sentences of every test set (the
  accent set's speakers, the hard-cases set's videos) were left out, as for v3.
- Every save of both stages was scored on a held-back dev slice (AMI, Common Voice, LibriSpeech other,
  VoxPopuli and YouTube clips, none in the test sets) through NowCaption's own caption pipeline. This
  save had the fewest word errors there (9.33%, v3 with the faster encoder 9.72%), with words showing
  about as soon as v3's. Only then was it run on the test sets below.
- The save (SHA-256): step0043775.nemo 59f2b5eb2faece6cd80255a8267f5c4f041cc71869a326a65bfaac684c31c37d.
- Export: ONNX with onnx 1.23.2, quantized by onnxruntime 1.31.0's quantize_dynamic (QUInt8 weights,
  per channel). Runs on sherpa-onnx 1.13.8, as shipped in NowCaption 1.2.x.

Files (SHA-256):

    3346e333cf42a42a48f7bd64c4627a719dbeba77c51eb69ebee2630a113fa943  encoder.int8.onnx
    47b74c5bfa2c66e1ca902055aa8715c8960f85bdc06d934d3c934fbc135969b2  decoder.int8.onnx
    5cd8f63e2b7a184fc13eef3ed7cbc4eb2cca65b0c5511121a3b0a91857fa03e9  joiner.int8.onnx
    618dc110fc2213886b52e063ff42329bbdf37a266ca7705184090fa5f39f3131  tokens.txt

Training speech (as v3: 3,348 hours prepared; only CC0 and CC-BY sources; speakers and utterances of
the test sets removed):

| Data | License |
|---|---|
| NVIDIA Granary, English YODAS part: YODAS2 videos (ESPnet / WAVLab) with NVIDIA's labels | audio CC-BY-3.0, labels CC-BY-4.0 |
| Common Voice 22.0, English (Mozilla) | CC0-1.0 |
| AMI Meeting Corpus (University of Edinburgh) | CC-BY-4.0 |
| LibriSpeech train-clean-100 and train-other-500 | CC-BY-4.0 |
| VoxPopuli English (Meta AI) | CC0-1.0 |
| Transcripts by parakeet-tdt-0.6b-v2 (NVIDIA) | CC-BY-4.0 |

Augmentation: MUSAN (music, noise and speech under Creative Commons licenses or in the public
domain, per file) and RIRS_NOISES (OpenSLR SLR28, Apache-2.0).

Measured against v3 on a Ryzen 7 7800X3D (sherpa-onnx 1.13.8, one thread), through NowCaption's own
caption pipeline (audio in 40 ms blocks, Whisper normalizer), on the ESB English test sets, 400 clips
each (AMI, Common Voice, Earnings-22, GigaSpeech, LibriSpeech clean and other, URGENT 2024, VoxPopuli).
v3's figures are the ones published with it, from the same scripts, settings and clips:

| | v3 | v4 |
|---|---|---|
| Continuous use (one long stream, 2 s pauses between clips): word errors, average of the 8 sets | 13.31% | 13.04% |
| Continuous use: time until a word shows in its final form, median / 90% / 95% / 99% | 0.210 / 0.406 / 0.472 / 0.619 s | 0.204 / 0.403 / 0.468 / 0.620 s |
| Fresh stream for every clip: word errors | 13.18% | 12.89% |
| Fresh stream for every clip: time until a word shows, median / 90% / 95% / 99% | 0.235 / 0.448 / 0.513 / 0.658 s | 0.226 / 0.435 / 0.501 / 0.646 s |
| 400 other clips from each set, never used to choose a model: continuous use / fresh stream | 12.43% / 12.19% | 12.33% / 11.94% |
| Accent test set (2,992 clips, 20 regions), continuous use, all clips | 13.78% | 13.47% |
| Hard cases (370 YouTube clips: gaming, names, numbers, live streams), continuous use, all clips | 9.07% | 8.82% |
| CPU seconds per second of audio: NowCaption alone (78 min of VoxPopuli) / eight captioning at once | 0.161 / 0.191 | 0.073 / 0.141 |
| Fed in real time for 10 minutes (LibriSpeech clean, speech model only): share of one core / one 160 ms step, median and slowest 1% | 17.7% / 28.1 and 37.0 ms | 7.2% / 11.1 and 13.4 ms |
| Peak memory of the whole caption process: alone / eight at once | 352 / 348 MB | 331 / 333 MB |
| Download | 127 MB | 111 MB |

Delays are percentiles over every timed word of the eight sets together (about 46,000 words). Taking
each set's percentile and averaging over the sets instead gives 0.222 / 0.416 / 0.601 s for v3 and
0.215 / 0.411 / 0.604 s for v4 (median / 90% / 99%) in continuous use.

Over noise alone (60 ten-second clips of MUSAN noise) neither model prints a word; over instrumental
music (40 clips of MUSAN music without vocals) v3 printed 35 words and v4 27, all in the same two clips.
MUSAN is also what both models' training mixed in as background, so these clips are not unseen audio.

Known weaknesses, all in continuous use (the app's normal mode) unless noted:

- Repeated words: in 4 of 9,762 clips v4 printed a word four or more times in a row, more often
  than it was said: twice for a word said once ("sir sir sir sir sir sir sird" for an unusual name,
  "fall fall fall fall falls"), twice one or two copies too many of a word said two or three times. v3
  did it once ("yeah" 13 times for 3). With a fresh stream per clip neither model did it.
- LibriSpeech test-other in benchmark/pipeline.py's 132-clip continuous test: 6.70% against 6.42%,
  worse in all six pause patterns. On the 400 ESB test-other clips it is 0.09 points worse (within
  chance) and on 400 others 0.19 better.
- On the 400 other clips per set, AMI and Earnings-22 are about 0.2 points worse (within chance). Of
  the accent regions with at least 100 clips, England north is 0.4 points worse (171 clips, within
  chance); no region is better or worse beyond chance, and with a fresh stream the accent set is about
  the same as with v3 (14.10% against 14.06%).
- Common Voice's first two words after a pause show 17 ms later at the median (0.421 against 0.404 s),
  though fewer of them are wrong (18.0% against 19.0%). The slowest 1% of all words show 1 ms later.
- The hard-cases references are the text two NVIDIA models agreed on, one of them the model that wrote
  v4's transcripts, which may favour v4 on that set.

See benchmark/README.md in the NowCaption repository for the full results, ranges and methods.
