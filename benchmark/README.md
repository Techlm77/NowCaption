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
| **NowCaption (FastConformer multi, 80 ms, CC-BY)** | **2.4%** | **7.0%** | **0.31–0.37 s** | **0.47–0.55 s** | **0.15** | **277 MB** |

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

## Music filter

**Filter out music** puts a streaming speech-enhancement model in front of the
recognizer. Candidates were run through NowCaption's caption pipeline (40 ms
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
through. The music filter is off by default because it costs accuracy on plain
speech (full pipeline, one run each):

| WER | No filter | DPDFNet2 |
|---|---|---|
| test-clean | 2.73% | 3.17% |
| test-clean + white noise at 10 dB SNR | 6.16% | 6.47% |
| test-other | 7.01% | 7.39% |

## Historical capture-to-worker-event results (scorer version 1)

These archived runs used the older `e2e.py`, with one clip from each speaker,
played in real time. That scorer could count a repeated word from the next clip
for the previous clip, and did not publish missed-word coverage. Treat the latency
figures below as historical results awaiting a rerun, rather than verified
performance of 1.0.4. The previous speech CPU denominator also included gaps
and settling time.

| | test-clean (40 speakers) | test-other (33 speakers) |
|---|---|---|
| Median lag | 0.31 s | 0.34 s |
| p90 lag | 0.55 s | 0.59 s |
| p99 lag | 1.09 s | 1.09 s |
| CPU over playback, gaps and settling | 0.19 core | 0.19 core |
| CPU during digital silence | 0.004 core | 0.004 core |
| Peak memory | 303 MB | 304 MB |

The current scorer (version 3) uses the worker's optional complete session trace
after normal caption formatting, rather than the moving caption excerpt. Words
remain available to the scorer after they scroll away, and repeated words keep
separate positions. No extra punctuation inference runs for the trace. Raw
recognition words are scored; GTK delivery and display remain outside the metric.
The trace adds text copying and recording to the measured process; CPU results
therefore include benchmark instrumentation.

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
is processed like speech. Corrected runs write `e2e-v3-*.json` and leave the
historical result files intact. No corrected native run was performed in this
repair environment.

Version 2 fixed clip-boundary attribution but scored moving caption excerpts;
long utterances could lose already displayed words and shift latency positions.
Those results also require a rerun with version 3. Dumped caption records now
contain complete recognition history, not screenshots or the two visible rows.

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
python bench.py sherpa --model ../models/nowcaption-fastconformer-en-80ms-cc-by-v1 \
    --name nowcaption --threads 1 --data data/test-clean
./build-april.sh
APRIL_LIB=april/build/libaprilasr.so python bench.py april --model april/april-english-dev-01110_en.april \
    --name april-upstream --threads 1 --data data/test-clean
PYTHONPATH=../src python e2e.py --data data/test-clean     # needs pactl and paplay
```

Results are written to `results/`. Files named `*-dummy-*` are from the
single-speaker set. A corrected capture benchmark result contains
`score_version: 3` and `metric_scope: capture_to_worker_event`; it must be rerun
on Linux with the real models before quoting new latency or CPU figures.
