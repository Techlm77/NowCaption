# Benchmark

How NowCaption 1.1.7 performs, and why it works the way it does. Everything was measured
on one machine (Ryzen 7 7800X3D, ONNX Runtime on the CPU, one thread).

The main test sets are a fixed random sample of 3–15 s utterances from LibriSpeech
(`get-data.py --set test-clean / test-other`): 4 per speaker, from 40 test-clean speakers
(160 clips, 3,151 words) and 33 test-other speakers (132 clips, 2,369 words). test-other is
the harder half. LibriSpeech is read-aloud audiobook speech, so calls, games, music,
overlapping voices and strong accents do worse; the [everyday speech](#everyday-speech-engine-only)
sets cover those.

There are three kinds of measurement:

- **Engine-only** (`bench.py`) feeds WAV audio straight into each speech engine, with a
  fresh stream for every clip. This compares engines.
- **Continuous use** (`pipeline.py`) runs the app's own caption pipeline (punctuation, line
  splitting, silence handling) on one stream, as the app runs, and can time each stage.
- **Real audio capture** (`e2e.py`) plays clips into a private PipeWire sink and captures
  them as the app does, through to the caption event.

## Against Live Captions (engine-only)

| Engine | WER test-clean | WER test-other | Median lag | p90 lag | CPU | RAM |
|---|---|---|---|---|---|---|
| April English (upstream Live Captions) | 8.1% | 17.9% | 0.42 s | 0.52–0.54 s | 0.21 | 895 MB |
| **NowCaption 1.1.7** | **2.5%** | **7.1%** | **0.23–0.30 s** | **0.39–0.47 s** | **0.15** | **283–284 MB** |

NowCaption makes about two-thirds fewer errors, shows words sooner and uses less CPU and a
third of the memory. Its speech model and engine are unchanged since 1.1.6, so these are
1.1.6's runs (`results/nowcaption-v2-*`): audio in 40 ms blocks as the app captures it,
with 0.96 s of silence before each new stream as the app primes it (`--warmup-ms 960`).

April is measured as upstream Live Captions by abb128 ships it: abb128/april-asr at commit
`8b52f65` (the one Live Captions pins), built against ONNX Runtime 1.14.1 as in its Flatpak
manifest, with the `april-english-dev-01110_en.april` model it downloads (`build-april.sh`
reproduces this). It was fed 100 ms blocks; fed 100 ms blocks, NowCaption's previous model
(1.1.5) had a median lag of 0.31–0.37 s. Like every engine here it runs in sync mode on one thread; the Live
Captions app itself runs April in its asynchronous real-time mode.

## Everyday speech (engine-only)

Since 1.1.6 NowCaption has its own speech model: NVIDIA's streaming FastConformer
fine-tuned on about 3,100 hours of CC-BY and CC0 English speech (podcasts and YouTube
videos, meetings, speeches, audiobooks), with the text NVIDIA's Parakeet TDT 0.6B v2 heard
in each clip, then averaged 50/50 with the original weights. Its card is in the model
archive's README.md; the training data is listed in [MODEL-LICENSES.md](../MODEL-LICENSES.md).

The test sets are the [ESB](https://huggingface.co/datasets/hf-audio/esb-datasets-test-only-sorted)
English test sets: 400 random clips from each (54,033 words), and to check the choice,
400 other clips from each (54,279 words) that were never used to pick between models.

| Test set | NVIDIA's model | NowCaption's own | NVIDIA's, other clips | Own, other clips | Median lag NVIDIA / own |
|---|---|---|---|---|---|
| AMI (meetings) | 24.5% | **21.1%** | 21.5% | **19.2%** | 0.40 / **0.32 s** |
| URGENT 2024 (noisy speech) | 24.5% | **20.8%** | 23.7% | **19.4%** | 0.40 / 0.40 s |
| Earnings-22 (business calls, accents) | 20.0% | **17.2%** | 17.7% | **16.3%** | 0.40 / 0.40 s |
| GigaSpeech (podcasts, YouTube) | 14.3% | **13.7%** | 13.9% | **12.8%** | 0.40 / **0.32 s** |
| Common Voice (cheap microphones, accents) | **17.7%** | 18.1% | 15.9% | **15.6%** | 0.48 / **0.40 s** |
| VoxPopuli (speeches, accents) | 9.0% | **8.9%** | 8.8% | **8.1%** | 0.32 / 0.32 s |
| LibriSpeech test-other | 7.4% | **6.8%** | 6.3% | 6.3% | 0.32 / 0.32 s |
| LibriSpeech test-clean | **2.8%** | 3.1% | 2.9% | 2.9% | 0.32 / 0.32 s |
| **Average** | 15.0% | **13.7%** | 13.9% | **12.6%** | |

Here WER is measured after the Whisper English normalizer (so British and American
spellings count as the same word), each clip comes after 1 s of silence on a fresh stream,
and audio is fed in 40 ms blocks. On the words both models got right, the own model shows
them 18 ms sooner on average. For comparison, Parakeet TDT 0.6B v2 itself, an offline model
too slow to caption live on a CPU, averages 8.7% on the first 400 clips. These sets were
scored with the model-training scripts, which are not in this repository.

On the read audiobooks of the first table, the own model makes slightly more errors than
NVIDIA's (2.51% / 7.09% against 2.28% / 6.80%, 7 more in each set; on test-clean about half
are old spellings such as "to day"), and shows its slowest tenth of words 80 ms sooner.

## Continuous use (caption pipeline)

The tables above start a fresh stream for every clip, as they must to compare engines.
The app keeps one stream running, so words appear sooner. Measured for 1.1.7 with
`pipeline.py`: the same clips joined by 2.0–2.1 s of digital silence, three random gap
patterns for test-clean and six for test-other (`--seed`), audio fed in 40 ms blocks:

| NowCaption 1.1.7 | WER | Mean lag | Median lag | p90 lag |
|---|---|---|---|---|
| test-clean | 2.78% | 0.173 s | 0.195 s | 0.341 s |
| test-other | 6.94% | 0.209 s | 0.233 s | 0.392 s |

WER varies by up to 0.4 points with the gap pattern alone. The first two words after a pause
are wrong about twice as often as the rest on test-other (14.1% against 6.4%, 33 clips,
three gap patterns), and no more often on test-clean (2.1% against 2.3%, 40 clips): the
model has heard nothing yet to go on. The captured audio is not the cause: played through
PipeWire and captured as the app captures it, each clip's first 300 ms match the original
within 0.1 dB. Feeding extra silence before speech resumes, or never pausing recognition in
silence, did not help.

## Real audio capture

Measured for 1.1.7 with `e2e.py` (scorer version 3): one clip from each speaker, played in
real time into a private sink with 3 s gaps, captured by GStreamer in 40 ms blocks as in the
app, then recognised and punctuated by the app's worker.

| NowCaption 1.1.7 | test-clean (40 speakers) | test-other (33 speakers) |
|---|---|---|
| Median lag | 0.26 s | 0.29 s |
| p90 lag | 0.40 s | 0.45 s |
| p99 lag | 0.53 s | 0.63 s |
| Timed words recognised correctly | 678 of 687 (98.7%) | 575 of 598 (96.2%) |
| CPU while a clip plays | 0.19 core | 0.19 core |
| CPU in the 3 s after each clip | 0.11 core | 0.11 core |
| CPU during digital silence | 0.004 core | 0.005 core |
| Peak memory | 321 MB | 317 MB |

The clock starts just before `paplay` launches, so the player's start-up is included (the
first audio reaches the app about 50 ms later), and it is not a hardware-synchronised audio
clock. GTK drawing and the screen are not included. The scorer reads the worker's complete
session trace, never credits a clip with words from the next one, and reports percentiles
over the words recognised correctly. The trace adds some copying, so CPU and memory include
that instrumentation. Capture failures, recognition errors and overload abort a run.

## Where the time goes

Measured with `pipeline.py --stages` (seed 0) in continuous use. CPU time per second of
audio, test-clean / test-other:

| Stage | ms per second of audio |
|---|---|
| Speech model (encoder and greedy search) | 153.8 / 153.8 |
| Punctuation and casing | 11.7 / 11.3 |
| Reading the recognised text | 2.3 / 1.7 |
| Audio features | 1.2 / 1.2 |
| Everything else (Python) | 0.5 / 0.5 |
| **Total** | **169 / 169** |

Nine tenths of the work is the speech model itself, in ONNX Runtime. A second ONNX Runtime
thread cuts a decode from 27 to 18 ms but costs 0.57 CPU seconds per second of audio
instead of 0.17 (its threads keep spinning between decodes), so the app uses one.

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

## Why NowCaption works the way it does

- **The stream is never reset at pauses.** On 30 clips joined by 0.4 s gaps
  (`endpoint_test.py`), resetting at every pause raised WER from 4.0% to 8.4%. Pauses only
  start a new caption line.
- **40 ms capture blocks.** Smaller blocks reach the model sooner, down to 40 ms. Measured
  with NVIDIA's model, median lag on test-clean / test-other is 0.306 / 0.366 s with 100 ms
  blocks, 0.267 / 0.345 s with 60 ms, 0.227 / 0.306 s with 40 ms and 0.228 / 0.306 s with
  20 ms, with the same WER and CPU (`bench.py --chunk-ms`).
- **0.96 s of silence before a new stream.** Without it, when speech starts on a stream's
  first sample, the own model shows words 80 ms later (median 0.305 / 0.385 s). 0.95 s would
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

In 1.1.6 the chosen model was fine-tuned into NowCaption's own ([everyday speech](#everyday-speech-engine-only)).

## How things are measured

- **WER** is word error rate after lower-casing and removing punctuation ("mr" counts as
  "mister"; digits are not expanded). Punctuation never changes the words.
- **Lag** is when a word first appeared correctly, including compute time, minus when the
  word ends in the audio. Word end times come from offline Parakeet TDT 0.6B v2 timestamps
  (`word_times.py`), used only where that model heard the word correctly.
- **CPU** is CPU seconds per second of audio. In `bench.py` it includes 1.5 s of silence
  after each clip; in `pipeline.py` it is the recognition thread's CPU time.

## Running it

Install `huggingface_hub pyarrow soundfile numpy sherpa-onnx` in a virtual environment.
`word_times.py` also needs the `sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8` model from the
sherpa-onnx `asr-models` release. For April, `build-april.sh` builds upstream april-asr with
ONNX Runtime 1.14.1 and downloads the model into `april/` (needs git, curl, cmake and a C
compiler).

```sh
python get-data.py --set test-clean        # also: --set test-other, or no option for the 73-clip set
python word_times.py --data data/test-clean
python bench.py sherpa --model ../models/nowcaption-fastconformer-en-80ms-cc-by-v2 \
    --name nowcaption-v2 --threads 1 --warmup-ms 960 --data data/test-clean
./build-april.sh
APRIL_LIB=april/build/libaprilasr.so python bench.py april --model april/april-english-dev-01110_en.april \
    --name april-upstream --threads 1 --chunk-ms 100 --data data/test-clean   # as published
PYTHONPATH=../src python pipeline.py --data data/test-clean --seed 0 --stages
PYTHONPATH=../src python e2e.py --data data/test-clean     # needs pactl and paplay
```

Results are written to `results/`: `pipeline-<data>-s<seed>.json` from `pipeline.py`,
`e2e-v3-*.json` from `e2e.py`, and the rest from `bench.py` (`*-dummy-*` from the
one-speaker screen). Older runs, such as `e2e-test-*.json` from a first scorer that could
credit a word to the wrong clip, are kept for reference; earlier versions' results are in
the git history.
