# Benchmark

There are two kinds of measurement here:

- **Engine-only** (`bench.py`) compares speech engines by feeding WAV audio straight
  into each one. This is how NowCaption's model was chosen.
- **Capture to worker event** (`e2e.py`) measures playback into a
  private PipeWire sink, then GStreamer capture, the recognition worker,
  punctuation and the caption event. It does not measure GTK event delivery or
  the time when text actually becomes visible on the screen.

All numbers come from one machine (Ryzen 7 7800X3D, ONNX Runtime on the CPU, one
thread). LibriSpeech is read-aloud audiobook speech, so calls, games, music,
overlapping voices and strong accents will do worse than these figures.

## Accuracy across speakers (engine-only)

The test sets are a fixed random sample of 3–15 s utterances from LibriSpeech
(`get-data.py --set test-clean / test-other`): 4 per speaker, from 40 test-clean
speakers (160 clips, 3,151 words) and 33 test-other speakers (132 clips, 2,369
words). test-other is the harder half of LibriSpeech's test data.

| Engine | WER test-clean | WER test-other | Median lag | p90 lag | CPU | RAM |
|---|---|---|---|---|---|---|
| April English (upstream Live Captions) | 8.1% | 17.9% | 0.42 s | 0.52–0.54 s | 0.21 | 895 MB |
| **NowCaption 1.1.6 (own model, 80 ms, CC-BY)** | **2.5%** | **7.1%** | **0.23–0.30 s** | **0.39–0.47 s** | **0.15** | **283–284 MB** |
| NowCaption 1.1.5 (NVIDIA FastConformer multi, 80 ms, CC-BY) | 2.4% | 7.0% | 0.23–0.31 s | 0.47 s | 0.15–0.16 | 271–273 MB |

**NowCaption 1.1.6** was measured with its own speech model and with 0.96 s of
silence fed to each new stream before the clip (`bench.py --warmup-ms 960`), as the
app does since 1.1.6; 1.1.5 was measured for 1.1.4 without it. Audio is fed in
40 ms blocks, as the app captures it, and the text is read as the app reads it.
April was measured with 100 ms blocks; fed 100 ms blocks, NowCaption 1.1.5 has a
median lag of 0.31–0.37 s ([block size](#capture-block-size-engine-only)).

On these read audiobooks the new model makes slightly more errors than the one it
was trained from, measured the same way (with the warm-up):

| Speech model | WER test-clean | WER test-other | Median lag | p90 lag |
|---|---|---|---|---|
| NVIDIA FastConformer multi (1.1.5) | **2.28%** | **6.80%** | 0.225 / 0.305 s | 0.464 / 0.544 s |
| NowCaption's own (1.1.6) | 2.51% | 7.09% | 0.225 / 0.304 s | **0.385 / 0.465 s** |

That is 7 more errors in each set. On test-clean about half are spellings such as
"endeavouring" and "today" where these old books have "endeavoring" and "to day"; the
rest, and those on test-other, are recognition errors. Words appear as soon, and the
slowest tenth 80 ms sooner. On everyday speech the new model makes fewer
errors ([next section](#everyday-speech-engine-only)). Without the warm-up, when
speech starts on a stream's very first sample, its median lag was 80 ms longer
(0.305 / 0.385 s).

**April** is measured as upstream Live Captions by abb128 ships it: abb128/april-asr
at commit `8b52f65` (the one Live Captions pins), built against ONNX Runtime 1.14.1 as
in its Flatpak manifest, with the `april-english-dev-01110_en.april` model it downloads
(`build-april.sh` reproduces this). Like every engine here it runs in sync mode on one
thread; the Live Captions app itself runs April in its asynchronous real-time mode.
April figures published before 1.0.9 came from a modified fork of Live Captions with
changes to April, not from upstream, and have been replaced.

**WER** is word error rate after lower-casing and removing punctuation ("mr" counts
as "mister"; digits are not expanded). Each clip starts from a fresh stream, as it
does for every engine.

**Lag** is when the engine first produced a word correctly, minus when the word
ends in the audio, including compute time. Word end times come from offline
Parakeet TDT 0.6B v2 timestamps (`word_times.py`), used only where that model heard
the word correctly.

**CPU** is CPU seconds per second of audio, including 1.5 s of silence after each
clip.

## Continuous use (caption pipeline)

The table above starts a fresh stream for every clip, as it must to compare engines.
The app keeps one stream running, so words appear sooner than that table shows. Measured
for 1.1.7 with the app's whole caption pipeline (punctuation, line splitting, silence
handling), the same clips joined by 2.0–2.1 s of digital silence, three random gap patterns
for test-clean and six for test-other, audio fed in 40 ms blocks:

| | WER | Mean lag | Median lag | p90 lag |
|---|---|---|---|---|
| test-clean | 2.78% | 0.174 s | 0.196 s | 0.342 s |
| test-other | 6.94% | 0.211 s | 0.235 s | 0.394 s |

WER varies by ±0.3 points with the gap pattern alone. The first two words after a pause
are wrong about twice as often as the rest on test-other (14.1% against 6.4%, 33 clips,
three gap patterns), and no more often on test-clean (2.1% against 2.3%, 40 clips). The
captured audio itself is intact: played through PipeWire and captured as the app captures
it, each clip's first 300 ms match the original within 0.1 dB. Feeding extra silence before
speech resumes, or never pausing recognition in silence, did not help.

## Where the time goes

Measured with timers around each stage of the caption pipeline in continuous use (one
thread, Ryzen 7 7800X3D). CPU time per second of audio, test-clean / test-other:

| Stage | ms per second of audio |
|---|---|
| Speech model's encoder | ≈154 / 155 |
| Punctuation and casing | 11.7 / 11.5 (15.6 / 14.2 before 1.1.7) |
| Reading the recognised text | 2.4 / 1.8 |
| Audio features | 1.2 / 1.2 |
| Greedy search (decoder and joiner) | ≈0.6 |
| All other Python code | 0.4 |

So nine tenths of the work is the speech model itself, in ONNX Runtime. A second ONNX
Runtime thread cuts a decode from 27 to 18 ms but costs 0.57 CPU seconds per second
of audio instead of 0.17 (its threads keep spinning between decodes), so the app uses one.

The time from the end of a word to the caption event is mostly the model's design. It
decodes 160 ms of audio at a time and needs 110 ms past each step (its 80 ms lookahead plus
audio features), which averages 157 ms per word; the decode then takes about 26 ms, and the
40 ms capture blocks add 10 ms. Punctuation adds about 5 ms when it runs, PipeWire up to one
21 ms quantum, and the screen one or two frames. The worker queue and the hand-over to GTK
take well under a millisecond.

## Everyday speech (engine-only)

NowCaption 1.1.6 has its own speech model: NVIDIA's model fine-tuned on about 3,100
hours of CC-BY and CC0 English speech (podcasts and YouTube videos, meetings, speeches,
audiobooks), with the text NVIDIA's Parakeet TDT 0.6B v2 heard in each clip, and then
averaged 50/50 with the original weights. Its card is in the model archive's README.md;
the training data is listed in [MODEL-LICENSES.md](../MODEL-LICENSES.md).

The test sets are the [ESB](https://huggingface.co/datasets/hf-audio/esb-datasets-test-only-sorted)
English test sets: 400 random clips from each (54,033 words), and to check the choice,
400 other clips from each (54,279 words) that were never used to pick between models.

| Test set | 1.1.5 | 1.1.6 | 1.1.5, other clips | 1.1.6, other clips | Median lag 1.1.5 / 1.1.6 |
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
spellings count as the same word), each clip comes after 1 s of silence on a fresh
stream, and audio is fed in 40 ms blocks. Lag is the same measure as above against
Parakeet timestamps; on the words both models got right, words appear 18 ms sooner on
average. For comparison, Parakeet TDT 0.6B v2 itself, an offline model too slow to
caption live on a CPU, averages 8.7% on the first 400 clips. These sets were scored with
the model-training scripts, which are not in this repository.

## Capture block size (engine-only)

NowCaption's capture hands audio to the recognizer in fixed blocks. Smaller blocks
reach the model sooner; below 40 ms there is no further gain. Since 1.0.9 the app
uses 40 ms (`bench.py --chunk-ms`):

| Block | WER test-clean | WER test-other | Median lag clean / other | p90 lag clean / other | CPU |
|---|---|---|---|---|---|
| 100 ms (before 1.0.9) | 2.44% | 6.96% | 0.306 / 0.366 s | 0.469 / 0.546 s | 0.159 |
| 60 ms | 2.44% | 6.96% | 0.267 / 0.345 s | 0.466 / 0.508 s | 0.160 |
| **40 ms** | **2.44%** | **6.96%** | **0.227 / 0.306 s** | **0.465 / 0.468 s** | **0.157** |
| 20 ms | 2.44% | 6.96% | 0.228 / 0.306 s | 0.465 / 0.467 s | 0.158 |

Through the whole caption pipeline (silence handling, line splitting and
punctuation, with clips joined by 2–2.1 s of digital silence at nine random
offsets), 40 ms and 100 ms blocks gave the same WER: 2.65% against 2.66% on
test-clean and 6.84% against 6.83% on test-other.

`modified_beam_search` is not available: sherpa-onnx supports only greedy search
for streaming NeMo transducers such as this model.

## Background filter (removed in 1.1.6)

Until 1.1.5, the optional **Background filter** put a streaming speech-enhancement
model in front of the recognizer. Candidates were run through NowCaption's caption pipeline (40 ms
blocks, punctuation, line splitting) on a 3 min 50 s pop song, scored against its
244 sung words (backing vocals in brackets excluded). The song is not included here.

| Filter | Words captioned | WER | Extra CPU | Streams live |
|---|---|---|---|---|
| None | 49 | 86.1% | — | — |
| GStreamer `webrtcdsp` noise suppression, very high | 53 | 83.6% | ~0.001 | yes |
| RNNoise 0.2 (xiph) | 87 | 82.8% | 0.009 | yes |
| GTCRN | 128 | 77.9% | 0.050 | yes, 24 ms delay |
| DPDFNet baseline | 136 | 71.7% | 0.049 | yes, 20 ms delay |
| **DPDFNet2 (used)** | **134** | **69.3%** | **0.096** | **yes, 20 ms delay** |
| Spleeter 2-stem vocals | 97 | 74.6% | 0.04 | no: separates long chunks |
| UVR MDX-NET Voc_FT vocals | 163 | 52.5% | 1.24 | no: slower than real time |

RNNoise and WebRTC are built for steady background noise and let most music
through. The background filter is off by default because it costs accuracy on plain
speech (full pipeline, one run each):

| WER | No filter | DPDFNet2 |
|---|---|---|
| test-clean | 2.73% | 3.17% |
| test-clean + white noise at 10 dB SNR | 6.16% | 6.47% |
| test-other | 7.01% | 7.39% |

1.1.6 removed it: even the best live filter missed most sung words, and it made
plain speech worse. Robustness to music has to come from the speech model itself.

## Capture to worker event

Measured for 1.1.4 with `e2e.py` (scorer version 3): one clip from each speaker,
played in real time into a private sink with 3 s gaps, captured by GStreamer in
40 ms blocks as in the app, then recognised and punctuated by the app's worker.

| | test-clean (40 speakers) | test-other (33 speakers) |
|---|---|---|
| Median lag | 0.26 s | 0.29 s |
| p90 lag | 0.40 s | 0.46 s |
| p99 lag | 0.54 s | 0.62 s |
| Timed words recognised correctly | 677 of 687 (98.5%) | 577 of 598 (96.5%) |
| CPU while a clip plays | 0.20 core | 0.20 core |
| CPU in the 3 s after each clip | 0.11 core | 0.11 core |
| CPU during digital silence | 0.005 core | 0.004 core |
| Peak memory | 313 MB | 312 MB |

The scorer uses the worker's optional complete session trace after normal
caption formatting, rather than the moving caption excerpt. Words remain
available to the scorer after they scroll away, and repeated words keep separate
positions. No extra punctuation inference runs for the trace. Raw recognition
words are scored; GTK delivery and display remain outside the metric. The trace
adds text copying and recording to the measured process, so CPU and peak memory
include benchmark instrumentation.

The scorer never uses an event at or after the next clip's start for the preceding
clip. It aligns the complete final text before scoring word occurrences, uses
at least three seconds of settling silence between clips,
and reports timed-reference words, matched-word coverage and misses alongside
percentiles. Percentiles describe the words recognised correctly, not all words.

The clock starts just before `paplay` launches, so subprocess and playback
startup delay are included; this is not a hardware-synchronised audio clock.
CPU is measured separately over playback subprocess intervals, settling gaps
and final digital silence. Capture failures, recognition errors and overload
abort a run. Silence that is not digitally silent, such as background noise,
is processed like speech. Results are in `results/e2e-v3-*.json`.

Earlier runs with scorer version 1 and 100 ms blocks (`results/e2e-test-*.json`)
reported a median of 0.31 / 0.34 s and a p99 of 1.09 s. That scorer could count a
repeated word from the next clip for the previous one and scored a moving caption
excerpt, so those figures are kept only for reference.

## Memory over time

Before 1.1.3, memory grew with every minute of speech: sherpa-onnx 1.13.8 leaks a
copy of the recognised text each time Python reads `result.text`, and NowCaption
read it after every 40 ms block. Real sessions reached 1.9 GB after 2.5 hours and
7.5 GB after 13 hours. 1.1.3 reads the same text from the result's tokens.

| | 1.1.2 | 1.1.3 |
|---|---|---|
| Caption pipeline, 30 min of looped speech, as fast as possible | 208 → 616 MB | 208 → 209 MB |
| Whole app, 15 min of speech played in real time | +7.7 MB per minute | +0.08 MB per minute |

Memory is anonymous resident memory after `malloc_trim`, from the end of the
first minute (warm-up) to the end of the run. `tests/test_memory.py 30` repeats
the first row with the real models and fails above
1 MB per audio minute.

1.1.3 still kept every finished transcript line in memory, and glibc held on to
about 45 MB that it had freed. 1.1.4 writes finished lines to an unnamed
temporary file, keeps only the lines the caption box shows, and returns freed
memory to the system once a minute (`malloc_trim`, about 0.3 ms):

| | 1.1.3 | 1.1.4 |
|---|---|---|
| Caption pipeline as the app runs it, minutes 50–140 of looped speech | +5.1 KB per minute | +0.3 KB per minute |
| Whole app on Wayland after 17 min of speech | 279.0 MB | 237.6 MB |

The whole-app run used the layer-shell overlay and the GPU renderer on a headless
sway, with both versions captioning the same speech. In the first 20–30 minutes
of speech, memory still grows once while the models' buffers and the
recognizer's text reach their working size (the text is reset at 20,000
characters); after that, 1.1.4 stays flat.

## How the model was chosen

This first screen used 73 LibriSpeech dev-clean utterances (1,150 words, 8
minutes, `get-data.py` with no options). They all come from **one speaker**
(1272), so they compare engines but don't show how they do across speakers; use
the table above for that. "Noisy" adds white noise at 10 dB SNR.

| Engine | WER clean | WER noisy | Median lag | p90 lag | CPU | RAM |
|---|---|---|---|---|---|---|
| April English (upstream Live Captions) | 12.6% | 24.1% | 0.42 s | 0.52 s | 0.21 | 895 MB |
| **FastConformer multi, 80 ms, CC-BY (NowCaption)** | **6.1%** | **10.2%** | **0.35 s** | **0.55 s** | **0.16** | **277 MB** |
| FastConformer 80 ms (NGC terms, not redistributable) | 6.3% | 9.9% | 0.41 s | 0.61 s | 0.16 | 276 MB |
| Nemotron Speech Streaming 0.6B, 560 ms | 4.7% | 8.4% | 0.61 s | 0.97 s | 0.25 | 1007 MB |
| Nemotron Speech Streaming 0.6B, 160 ms (2 threads) | 5.6% | 10.2% | 0.41 s | 0.67 s | 1.03 | 975 MB |

On continuous audio (30 of these clips joined by 0.4 s gaps, `endpoint_test.py`),
NowCaption's model reaches 4.0% WER. Resetting the stream at every pause raises it
to 8.4%, which is why the app never resets at pauses.

Screened and rejected:

- **Parakeet unified 0.6B, 240 ms:** slower than real time on 2 threads.
- **Kroko streaming Zipformer:** about 1 s of lag.
- **Nemotron 3.5 multilingual, 320 ms:** 7.1% WER on a 6-clip screen.

## Running it

Install `huggingface_hub pyarrow soundfile numpy sherpa-onnx` in a virtual
environment. `word_times.py` also needs the `sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8`
model from the sherpa-onnx `asr-models` release. For April, `build-april.sh` builds
upstream april-asr with ONNX Runtime 1.14.1 and downloads the model into `april/`
(needs git, curl, cmake and a C compiler).

```sh
python get-data.py --set test-clean        # also: --set test-other, or no option for the 73-clip set
python word_times.py --data data/test-clean
python bench.py sherpa --model ../models/nowcaption-fastconformer-en-80ms-cc-by-v2 \
    --name nowcaption-v2 --threads 1 --warmup-ms 960 --data data/test-clean
./build-april.sh
APRIL_LIB=april/build/libaprilasr.so python bench.py april --model april/april-english-dev-01110_en.april \
    --name april-upstream --threads 1 --chunk-ms 100 --data data/test-clean   # as published
PYTHONPATH=../src python e2e.py --data data/test-clean     # needs pactl and paplay
```

Results are written to `results/`. Files named `*-dummy-*` are from the
single-speaker set. Capture results from the current scorer are `e2e-v3-*.json`
(`score_version: 3`, `metric_scope: capture_to_worker_event`).
