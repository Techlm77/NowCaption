# Benchmark

How NowCaption 1.2.0 performs, and why it works the way it does. Everything was measured on
one machine (Ryzen 7 7800X3D, ONNX Runtime 1.28 on the CPU through sherpa-onnx 1.13.8, one
thread) on 26 October 2026. 1.2.0 changes the speech model: NowCaption's own v3
(`nowcaption-fastconformer-en-80ms-cc-by-v3`) replaces v2. Where a table compares the two, both
were run the same day, on the same machine, data and settings.

There are four kinds of measurement:

- **Engine-only** (`bench.py`) feeds WAV audio straight into each speech engine, with a
  fresh stream for every clip. This compares engines.
- **Streaming** (`stream.py`) runs the app's own caption pipeline over a whole test set as one
  live stream, either continuously (as the app runs) or with a fresh stream for every clip. It
  is used for everyday speech and accents, and for the engines other Linux apps use.
- **Continuous use** (`pipeline.py`) runs the caption pipeline on one stream of LibriSpeech
  clips, and can time each stage.
- **Real audio capture** (`e2e.py`) plays clips into a private PipeWire sink and captures
  them as the app does, through to the caption event.

The LibriSpeech sets are a fixed random sample of 3–15 s utterances (`get-data.py --set
test-clean / test-other`): 4 per speaker, from 40 test-clean speakers (160 clips, 3,151 words)
and 33 test-other speakers (132 clips, 2,369 words). test-other is the harder half. LibriSpeech
is read-aloud audiobook speech, so calls, games, music, overlapping voices and strong accents do
worse; the [everyday speech](#everyday-speech-streaming) and [accent](#accents-streaming) sets
cover those.

## What's new in 1.2.0: speech model v3

v3 continues v2's training on about 3,300 hours of CC0 and CC-BY speech, in items that join
several clips with pauses between them, so it keeps working after silences in a long stream
([how it was made](#how-the-model-was-chosen)). On the eight everyday test sets below, through
the app's own caption pipeline:

| | v2 (1.1.x) | **v3 (1.2.0)** | Change, with its 95% range |
|---|---|---|---|
| Words wrong, continuous use | 17.31% | **13.31%** | −4.01 points (−4.45 to −3.59) |
| Words wrong, fresh stream per clip | 13.51% | **13.18%** | −0.34 points (−0.65 to −0.03) |
| The same, on 400 other clips per set never used to choose the model: continuous / fresh | 16.18% / 12.65% | **12.43% / 12.19%** | −3.76 (−4.16 to −3.35) / −0.46 (−0.75 to −0.16) |
| First two words after a pause, continuous use | 28.4% wrong | **17.4%** wrong | |
| Time until a word shows, continuous use: median / 90% / 99% | 0.219 / 0.399 / 0.591 s | 0.210 / 0.406 / 0.619 s | median −13 ms (−15 to −10), 90% −6 ms (−9 to −2) on the words both got right |
| Time until a word shows, fresh stream: median / 90% / 99% | 0.317 / 0.528 / 0.736 s | **0.235 / 0.448 / 0.658 s** | median −82 ms (−84 to −80) |
| Accent test set (2,992 clips, 20 regions), continuous use | 17.24% | **13.78%** | −3.46 (−3.86 to −3.04); 16 of 20 regions better beyond chance, none worse beyond chance |
| Clips that repeat one word 4+ times ("the the the the") | 6 | **0** | |
| CPU, real-time continuous speech (share of one core) | 17.2% | 17.7% | |
| Peak memory of the caption process | 321 MB | 353 MB | |
| Download | 101 MB | 127 MB | |

A change counts as beyond chance when its whole 95% range lies on one side of zero: the clips
of each set are drawn again 2,000 times (the same draw for both models), and the range covers
the middle 95% of the changes.

**Known limitations of v3:**

- **Short sentences right after a pause show a little later than with v2.** On the accent set,
  which is mostly short sentences, words show 26 ms later at the median (0.248 against
  0.217 s; on the words both got right, +26 ms, +22 to +29) and 44 ms later at the 90% mark,
  in continuous use. Common Voice's first two words after a pause show 26 ms later (0.404
  against 0.378 s), though far fewer of them are wrong (19.0% against 41.1%). Longer speech
  (meetings, videos, calls, audiobooks) shows sooner than with v2.
- **On a fresh stream, LibriSpeech test-clean is 0.38 points worse** (+0.07 to +0.73), and
  URGENT 2024 +0.88 (0.00 to +1.71). Every other set is better or within chance.
- **The slowest 1% of words** show about 28 ms later in continuous use.
- **On a fresh stream, accents are no better than v2** (−0.08 points, within chance): v3's
  accent gains come in continuous use, which is how the app runs.
- It uses about 30 MB more memory, from int8 weights with one scale per channel instead of per
  tensor (more accurate, slightly bigger).

**The targets set before training, and how v3 did.** They were written down on 24 October 2026,
before any training, for the default model:

| Target | v3 | Met? |
|---|---|---|
| Better than v2 on average and on accents, and no set worse beyond chance by more than 0.5 points | 8 sets −4.01 / −0.34 points (continuous / fresh), accents −3.46, no set worse beyond chance by more than 0.5 | **Yes** |
| Better than v2 in every accent region | 16 of 20 better beyond chance (continuous use); the four smallest regions (5–15 clips) within chance, and one of them, US Hispanic (5 clips), 5.0 points worse | **No** (not shown for the four smallest regions) |
| At most 12.5% words wrong on the eight sets | 13.31% continuous, 13.18% fresh; 12.43% and 12.19% on the 400 other clips per set | **No** (only on the other clips) |
| Median / 90% time until a word shows, continuous use: at most 0.20 / 0.35 s | 0.210 / 0.406 s on the eight sets; 0.162 / 0.315 s on LibriSpeech clean ([`pipeline.py`](#continuous-use-caption-pipeline)) | **No** on the everyday sets |
| CPU: at most 15% of one 7800X3D core | 17.7% in sustained real-time use (v2: 17.2%) | **No** |
| CPU on an older 4-core PC (about 2017): real-time factor at most 0.3 | Not measured: no such PC was available | Unknown |
| Memory of the whole app: at most 450 MB | WHOLE_APP_RSS | WHOLE_APP_MET |
| Download: at most 150 MB | 127 MB | **Yes** |

A second, larger model ("Large", opt-in) was planned with targets of its own. It was not made for
1.2.0.

## Against Live Captions (engine-only)

| Engine | WER test-clean | WER test-other | Median lag | p90 lag | CPU | RAM |
|---|---|---|---|---|---|---|
| April English (upstream Live Captions) | 8.1% | 17.9% | 0.42 s | 0.52–0.54 s | 0.21 | 895 MB |
| NowCaption 1.1.x (speech model v2) | 2.5% | 7.1% | 0.23–0.31 s | 0.39–0.47 s | 0.15 | 274–278 MB |
| **NowCaption 1.2.0 (speech model v3)** | **2.7%** | **6.9%** | **0.15–0.23 s** | **0.31–0.39 s** | **0.15** | **307–311 MB** |

NowCaption makes about two-thirds fewer errors than April, shows words sooner and uses less CPU
and a third of the memory. Audio in 40 ms blocks as the app captures it, with 0.96 s of silence
before each new stream as the app primes it (`--warmup-ms 960`). On test-clean v3 makes 5 more
errors than v2 (84 against 79 in 3,151 words); on test-other 5 fewer.

April is measured as upstream Live Captions by abb128 ships it: abb128/april-asr at commit
`8b52f65` (the one Live Captions pins), built against ONNX Runtime 1.14.1 as in its Flatpak
manifest, with the `april-english-dev-01110_en.april` model it downloads (`build-april.sh`
reproduces this). It was fed 100 ms blocks; fed 100 ms blocks, NowCaption's 1.1.5 model had a
median lag of 0.31–0.37 s. Like every engine here it runs in sync mode on one thread; the Live
Captions app itself runs April in its asynchronous real-time mode.

## Everyday speech (streaming)

The [ESB](https://huggingface.co/datasets/hf-audio/esb-datasets-test-only-sorted) English test
sets, a fixed random sample of 400 clips from each (`get-esb.py`), joined into one session per set
with 2.0–2.1 s of silence between clips and fed in 40 ms blocks through the app's caption
pipeline on one thread (`stream.py`). Words are counted after the Whisper English normalizer, so
British and American spellings count as the same word. Words wrong:

| Test set | v2, continuous | **v3, continuous** | Change (95% range) | v2, fresh | **v3, fresh** | Change (95% range) |
|---|---|---|---|---|---|---|
| AMI (meetings) | 25.8% | **16.4%** | −9.35 (−11.2 to −7.5) | 19.8% | **16.5%** | −3.23 (−4.7 to −1.9) |
| Common Voice (cheap microphones, accents) | 27.5% | **17.5%** | −10.02 (−11.9 to −8.3) | 18.3% | 17.2% | −1.08 (−2.4 to +0.2) |
| URGENT 2024 (noisy speech) | 28.2% | **22.1%** | −6.05 (−7.5 to −4.7) | 20.7% | 21.6% | +0.88 (0.0 to +1.7) |
| Earnings-22 (business calls, accents) | 19.8% | **16.8%** | −2.99 (−4.1 to −1.9) | 17.1% | 16.6% | −0.54 (−1.5 to +0.4) |
| GigaSpeech (podcasts, YouTube) | 16.4% | **14.0%** | −2.45 (−3.5 to −1.5) | 13.3% | 13.6% | +0.21 (−0.3 to +0.8) |
| VoxPopuli (speeches, accents) | 9.5% | **9.0%** | −0.52 (−1.0 to −0.1) | 9.0% | 9.2% | +0.19 (−0.2 to +0.6) |
| LibriSpeech test-other | 8.1% | **7.3%** | −0.73 (−1.3 to −0.2) | 7.0% | 7.5% | +0.46 (−0.1 to +1.0) |
| LibriSpeech test-clean | 3.3% | 3.4% | +0.06 (−0.4 to +0.5) | 2.9% | 3.3% | +0.38 (+0.1 to +0.7) |
| **Average** | **17.31%** | **13.31%** | **−4.01 (−4.45 to −3.59)** | **13.51%** | **13.18%** | **−0.34 (−0.65 to −0.03)** |

Continuous use is how the app runs: one stream, never reset. v2 lost words when someone started
talking after a pause, so it made many more errors in continuous use than with a fresh stream
per clip; v3 makes about the same in both. 400 other clips from each set (`get-esb.py --fresh`),
never used to choose the model, give the same picture: 16.18% → 12.43% in continuous use and
12.65% → 12.19% fresh, with no set worse beyond chance. Full tables, including the first words
after a pause and the per-set ranges, are in [`results/1.2.0/`](results/1.2.0/).

## Accents (streaming)

2,992 clips in 20 regions, from Common Voice 22's accent tags (CC0), the Edinburgh International
Accents of English Corpus (CC-BY-SA-4.0), Google's British Isles recordings (OpenSLR 83,
CC-BY-SA-4.0) and CORAAL for African-American English (CC-BY-NC-SA-4.0), used only for testing
(`results/1.2.0/accents.md`; the script that builds this set is not in this repository yet).
Words wrong in continuous use, v2 → v3:

| Region | v2 | v3 | Region | v2 | v3 |
|---|---|---|---|---|---|
| Africa | 25.8% | 23.0% | Scotland | 13.3% | 10.6% |
| Australia and New Zealand | 13.8% | 9.5% | UK general | 18.8% | 13.0% |
| Canada | 14.3% | 7.6% | US African-American English | 21.4% | 19.9% |
| England, Midlands | 8.7% | 6.2% | US general | 18.8% | 12.1% |
| England, North | 7.6% | 6.0% | US regional (Midwest, West, Northeast, South, Hispanic) | 13.8% | 10.7% |
| England, South | 12.1% | 10.4% | Wales | 9.4% | 7.1% |
| India and South Asia | 24.4% | 17.7% | Ireland and Northern Ireland | 22.6% | 19.8% |
| Other second-language English | 20.5% | 17.7% | **All 2,992 clips** | **17.24%** | **13.78%** |

16 of the 20 regions are better beyond chance and none is worse beyond chance. The other four
(Northern Ireland, US Hispanic, US Northeast and US South) have only 5–15 clips each, too few to
tell: three of them are better and US Hispanic is 5.0 points worse (0.0 to +9.3). With a fresh stream per clip every region is within chance of v2 (all clips: 14.18% →
14.10%).

## Continuous use (caption pipeline)

`pipeline.py`: the LibriSpeech clips joined by 2.0–2.1 s of digital silence, three random gap
patterns for test-clean and six for test-other (`--seed`), audio fed in 40 ms blocks:

| | WER | Mean lag | Median lag | p90 lag |
|---|---|---|---|---|
| test-clean, v2 | 2.78% | 0.171 s | 0.194 s | 0.340 s |
| **test-clean, v3** | **2.74%** | **0.137 s** | **0.162 s** | **0.315 s** |
| test-other, v2 | 6.94% | 0.208 s | 0.232 s | 0.390 s |
| **test-other, v3** | **6.42%** | **0.186 s** | **0.210 s** | **0.380 s** |

WER varies by up to 0.9 points with the gap pattern alone. v2's numbers match the ones published
for 1.1.7 to within 2 ms. The first two words after a pause are wrong about twice as often as the
rest (on the everyday sets, 17.4% against 9.9% with v3): the model has heard nothing yet to go
on.

## Real audio capture

Measured with `e2e.py` (scorer version 3): one clip from each speaker, played in real time into a
private sink with 3 s gaps, captured by GStreamer in 40 ms blocks as in the app, then recognised
and punctuated by the app's worker.

| | test-clean, v2 | **test-clean, v3** | test-other, v2 | **test-other, v3** |
|---|---|---|---|---|
| Median lag | 0.254 s | **0.201 s** | 0.295 s | **0.240 s** |
| p90 lag | 0.391 s | **0.364 s** | 0.453 s | **0.402 s** |
| p99 lag | 0.525 s | 0.534 s | 0.638 s | **0.588 s** |
| Timed words recognised correctly | 681 of 687 (99.1%) | 678 of 687 (98.7%) | 574 of 598 (96.0%) | 574 of 598 (96.0%) |
| CPU while a clip plays | 0.192 core | 0.195 core | 0.184 core | 0.191 core |
| CPU in the 3 s after each clip | 0.108 core | 0.110 core | 0.104 core | 0.108 core |
| CPU during digital silence | 0.005 core | 0.008 core | 0.007 core | 0.008 core |
| Peak memory | 321 MB | 353 MB | 317 MB | 350 MB |

The clock starts just before `paplay` launches, so the player's start-up is included (the
first audio reaches the app about 50 ms later), and it is not a hardware-synchronised audio
clock. GTK drawing and the screen are not included. The scorer reads the worker's complete
session trace, never credits a clip with words from the next one, and reports percentiles
over the words recognised correctly. The trace adds some copying, so CPU and memory include
that instrumentation. Capture failures, recognition errors and overload abort a run.

## Where the time goes

Measured with `pipeline.py --stages` (seed 0) in continuous use, v3. CPU time per second of
audio, test-clean / test-other:

| Stage | ms per second of audio |
|---|---|
| Speech model (encoder and greedy search) | 155.6 / 154.8 |
| Punctuation and casing | 11.6 / 11.0 |
| Reading the recognised text | 2.4 / 1.7 |
| Audio features | 1.1 / 1.1 |
| Everything else (Python) | 0.5 / 0.5 |
| **Total** | **171 / 169** |

Nine tenths of the work is the speech model itself, in ONNX Runtime. A second ONNX Runtime
thread cuts a decode from 27 to 18 ms but costs 0.57 CPU seconds per second of audio
instead of 0.17 (its threads keep spinning between decodes), so the app uses one. Fed in real
time for 10 minutes of continuous speech, one model step takes 28 ms at the median and 37 ms at
the slowest 1% with v3 (27 and 30 ms with v2): each covers 160 ms of audio, so captions never
fall behind.

The time from the end of a word to the caption event is mostly the model's design. It
decodes 160 ms of audio at a time and needs 110 ms past each step (its 80 ms lookahead plus
audio features), which averages 157 ms per word; the decode then takes about 26 ms, and the
40 ms capture blocks add 10 ms. Punctuation adds about 5 ms when it runs, PipeWire up to one
21 ms quantum, and the screen one or two frames. The worker queue and the hand-over to GTK
take well under a millisecond.

## Improvements by version

| Version | Change | Measured effect |
|---|---|---|
| 1.0.9 | Audio reaches the model in 40 ms blocks instead of 100 ms | Median lag 0.31 → 0.23 s (test-clean, engine-only), same accuracy |
| 1.1.3 | sherpa-onnx's text leak worked around | Whole app +7.7 → +0.08 MB per minute of speech (sessions had reached 7.5 GB after 13 hours) |
| 1.1.4 | Finished transcript lines go to an unnamed temporary file; freed memory goes back to the system | Whole app after 17 min of speech 279 → 238 MB |
| 1.1.6 | NowCaption's own speech model | Everyday speech 15.0% → 13.7% words wrong, meetings 24.5% → 21.1%; slowest tenth of words 80 ms sooner |
| 1.1.6 | Background filter removed | It made clear speech worse when on (2.73% → 3.17%) and still missed most sung words |
| 1.1.7 | Punctuation runs a quarter less often | 15.6 → 11.7 ms of CPU per second of audio (test-clean), finished text identical |
| 1.2.0 | Speech model v3 | Everyday speech in continuous use 17.3% → 13.3% words wrong, meetings 25.8% → 16.4%; first words after a pause 28% → 17% wrong; words show 13 ms sooner (median) |

## Why NowCaption works the way it does

- **The stream is never reset at pauses.** On 30 clips joined by 0.4 s gaps
  (`endpoint_test.py`), resetting at every pause raised WER from 4.0% to 8.4%. Pauses only
  start a new caption line. Speech model v3 was trained for this, on items that join clips
  with pauses between them.
- **40 ms capture blocks.** Smaller blocks reach the model sooner, down to 40 ms. Measured
  with NVIDIA's model, median lag on test-clean / test-other is 0.306 / 0.366 s with 100 ms
  blocks, 0.267 / 0.345 s with 60 ms, 0.227 / 0.306 s with 40 ms and 0.228 / 0.306 s with
  20 ms, with the same WER and CPU (`bench.py --chunk-ms`).
- **0.96 s of silence before a new stream.** Without it, when speech starts on a stream's
  first sample, speech model v2 showed words 80 ms later (median 0.305 / 0.385 s). 0.95 s would
  line the blocks up with the model's decodes, 10 ms sooner, but made 0.1 points more errors
  on test-other in 5 of 6 continuous runs.
- **Greedy search.** sherpa-onnx supports only greedy search for streaming NeMo transducers.
- **No music filter.** Live speech-enhancement filters were tried on a 3 min 50 s pop song
  (244 sung words, not included here): without a filter 86.1% of the words were wrong; the
  best live filter, DPDFNet2, got that to 69.3% for 0.096 extra CPU but made clear speech
  worse (2.73% → 3.17%). RNNoise and WebRTC noise suppression let most music through, and
  vocal separators (Spleeter, UVR MDX-NET) cannot run live. Robustness to music has to come
  from the speech model.
- **Memory stays flat.** Over minutes 50–140 of looped speech the caption pipeline grows
  0.3 KB per minute; `tests/test_memory.py 30` fails above 1 MB per audio minute.

## How the model was chosen

NVIDIA's streaming FastConformer (80 ms lookahead, CC-BY-4.0) was chosen from a first
screen of 73 LibriSpeech dev-clean clips from one speaker (`get-data.py` with no options;
"noisy" adds white noise at 10 dB SNR): 6.1% WER clean and 10.2% noisy, 0.35 s median lag,
0.16 CPU and 277 MB, against April's 12.6% and 24.1%. Also screened:

- **Nemotron Speech Streaming 0.6B:** more accurate at 560 ms (4.7% / 8.4%) but 0.61 s
  median lag and about 1 GB of memory; at 160 ms it needed 1.03 CPU cores.
- **FastConformer 80 ms under NVIDIA's NGC terms:** about the same, but not redistributable.
- **Parakeet unified 0.6B, 240 ms:** slower than real time on 2 threads.
- **Kroko streaming Zipformer:** about 1 s of lag.
- **Nemotron 3.5 multilingual, 320 ms:** 7.1% WER on a 6-clip screen.

In 1.1.6 the chosen model was fine-tuned into NowCaption's own speech model v2. For 1.2.0, v2
was trained further on one GPU (an RTX 5060 Ti, 16 GB) in three runs of the same recipe,
changing one thing at a time and scoring a held-back dev slice every two hours. Each run's pass
and fail rules were written down before its first score:

- **Run 1** cut the words lost after pauses, but words showed later the longer it trained. Its
  SpecAugment time masks hid whole words in long training items; shorter masks stopped the
  drift but did not undo it.
- **Run 2** added more single short sentences to the training mix. That made words later still
  and did not help Common Voice.
- **Run 3**, with run 1's mix, short masks and FastEmit 0.02, kept the accuracy gains with
  words as soon as v2's. Its accuracy stopped improving after 4 hours, while the time a word
  takes to show swung by 20–40 ms from one save to the next.

The released v3 blends two of run 3's saves in weight space: three quarters of the save after
4 hours and one quarter of the save after 8 hours. That kept the 4-hour save's speed with more
of the 8-hour save's accuracy, and on the eight test sets it is better than the 4-hour save
beyond chance (−0.34 points continuous, −0.19 fresh) for 5–8 ms of delay. It was then
confirmed on the accent set and on 400 other clips per set that played no part in the choice.
The model card in the release archive lists the training data, the two saves and the export.

**Why only int8.** The same blended weights were also exported in three other number formats and
measured on the same eight sets (continuous use, against the shipped int8):

| Format | Download | Words wrong | Time until a word shows, median | CPU | Peak memory |
|---|---|---|---|---|---|
| **int8, per channel (shipped)** | 127 MB | 13.31% | 0.210 s | 0.191 | 348 MB |
| int4 matrices, int8 convolutions | 88 MB | 13.90% (+0.60, worse beyond chance) | 0.221 s | 0.220 | 274 MB |
| float16 | 229 MB | 13.27% (within chance) | 0.230 s | 0.350 | 853 MB |
| float32 | 456 MB | 13.32% (within chance) | 0.230 s | 0.344 | 928 MB |

Full precision made no measurable difference to accuracy but showed words 20 ms later, with about
80% more CPU and 2.5 times the memory; int4 saved 74 MB of memory but made more mistakes, beyond
chance on Common Voice, AMI and Earnings-22. So 1.2.0 ships int8 only, and the app has no
setting to change it.

## How things are measured

- **WER** in `bench.py`, `pipeline.py` and `e2e.py` is word error rate after lower-casing and
  removing punctuation ("mr" counts as "mister"; digits are not expanded). `stream.py` and
  `compare.py` use the Whisper English normalizer instead (as the Open ASR Leaderboard does),
  which also treats British and American spellings and written numbers as the same word.
  Punctuation never changes the words.
- **Lag** in `bench.py`, `pipeline.py` and `e2e.py` is when a word first appeared correctly,
  including compute time, minus when the word ends in the audio. `stream.py` reports both that
  ("first") and when the word stopped changing ("settled", its final form); NowCaption never
  rewrites a word, so the two are the same for it. Word end times come from offline Parakeet
  TDT 0.6B v2 timestamps (`word_times.py`), used only where that model heard the word
  correctly. Changes in lag are compared on the words both models got right.
- **CPU** is CPU seconds per second of audio. In `bench.py` it includes 1.5 s of silence
  after each clip; in `pipeline.py` it is the recognition thread's CPU time; in `stream.py`
  the whole process's.
- **Why the tables differ.** The same model gives different numbers on different clips and in
  different modes: the LibriSpeech tables use the 160 and 132 clips of `get-data.py`, the
  everyday tables 400 other LibriSpeech clips from ESB; a fresh stream per clip avoids the
  words lost after pauses, so v2 makes 13.5% errors fresh and 17.3% in continuous use on the same
  clips; and the two normalizers count some words differently. 1.1.6 reported 13.7% for v2 on
  the same 400 ESB clips per set, scored by the training scripts with 1 s of silence before each
  clip and no caption pipeline; `stream.py` gives 13.51% for v2 fresh, through the app's own
  pipeline. Compare numbers only within one table.

## Running it

Install `huggingface_hub pyarrow soundfile soxr numpy sherpa-onnx jiwer whisper-normalizer
rapidfuzz` in a virtual environment. `word_times.py` also needs the
`sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8` model from the sherpa-onnx `asr-models` release.
For April, `build-april.sh` builds upstream april-asr with ONNX Runtime 1.14.1 and downloads
the model into `april/` (needs git, curl, cmake and a C compiler).

```sh
python get-data.py --set test-clean        # also: --set test-other, or no option for the 73-clip set
python word_times.py --data data/test-clean
python bench.py sherpa --model ../models/nowcaption-fastconformer-en-80ms-cc-by-v3 \
    --name nowcaption-v3 --threads 1 --warmup-ms 960 --data data/test-clean
./build-april.sh
APRIL_LIB=april/build/libaprilasr.so python bench.py april --model april/april-english-dev-01110_en.april \
    --name april-upstream --threads 1 --chunk-ms 100 --data data/test-clean   # as published
PYTHONPATH=../src python pipeline.py --data data/test-clean --seed 0 --stages
PYTHONPATH=../src python e2e.py --data data/test-clean     # needs pactl and paplay

# everyday speech: the eight ESB sets, continuous use and a fresh stream per clip
python get-esb.py                          # --fresh for the 400 other clips per set
for s in data/esb/*/; do python word_times.py --data $s; done
for s in data/esb/*/; do
  python stream.py app:../models/nowcaption-fastconformer-en-80ms-cc-by-v3 --data $s --name v3-cont
  python stream.py appreset:../models/nowcaption-fastconformer-en-80ms-cc-by-v3 --data $s --name v3-fresh
done                                       # the same with the v2 folder and --name v2-cont / v2-fresh
python compare.py --models v2 v3 --base v2
```

Results are written to `results/`: `pipeline-<data>-s<seed>.json` from `pipeline.py`,
`e2e-v3-*.json` from `e2e.py` (scorer version 3), `stream/` from `stream.py`, and the rest from
`bench.py` (`*-dummy-*` from the one-speaker screen). `results/1.2.0/` has `compare.py`'s
tables for the 1.2.0 release; the per-clip results behind them are attached to the GitHub release
as `nowcaption-1.2.0-benchmark-results.tar.xz`: every clip's word errors and word times, and what
NowCaption recognised for the sets whose licence allows passing on text from them (LibriSpeech, AMI,
Common Voice and VoxPopuli). Reference transcripts are left out, as some test sets' licences don't
allow passing them on. Older
runs, such as `e2e-test-*.json` from a first scorer that could credit a word to the wrong clip,
are kept for reference; earlier versions' results are in the git history.
