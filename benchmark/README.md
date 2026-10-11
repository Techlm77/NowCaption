# Benchmark

There are two kinds of measurement here:

- **Engine-only** (`bench.py`) compares speech engines by feeding WAV audio straight
  into each one. This is how NowCaption's model was chosen.
- **End-to-end** (`e2e.py`) measures the actual app pipeline: playback into a
  private PipeWire sink, then GStreamer capture, the recognition worker,
  punctuation and the caption event. Only drawing the text is left out, which adds
  at most one frame.

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
| April English (Live Captions) | 7.8% | 16.0% | 0.40 s | 0.49–0.52 s | 0.29 | 908 MB |
| **NowCaption (FastConformer multi, 80 ms, CC-BY)** | **2.4%** | **7.0%** | **0.31–0.37 s** | **0.47–0.55 s** | **0.15** | **277 MB** |

**WER** is word error rate after lower-casing and removing punctuation ("mr" counts
as "mister"; digits are not expanded). Each clip starts from a fresh stream, as it
does for every engine.

**Lag** is when the engine first produced a word correctly, minus when the word
ends in the audio, including compute time. Word end times come from offline
Parakeet TDT 0.6B v2 timestamps (`word_times.py`), used only where that model heard
the word correctly.

**CPU** is CPU seconds per second of audio, including 1.5 s of silence after each
clip.

## The whole app (end-to-end)

These runs used `e2e.py`, with one clip from each speaker of the test sets above,
played in real time.

| | test-clean (40 speakers) | test-other (33 speakers) |
|---|---|---|
| Median lag | 0.31 s | 0.34 s |
| p90 lag | 0.55 s | 0.59 s |
| p99 lag | 1.09 s | 1.09 s |
| CPU while speech plays | 0.19 core | 0.19 core |
| CPU during digital silence | 0.004 core | 0.004 core |
| Peak memory | 303 MB | 304 MB |

Here **lag** is when a word first appears correctly in a caption, minus when it
ends in the audio. The clock starts just before `paplay` starts, so the figures
slightly overstate the delay. **CPU** covers the app's capture, recognition and
punctuation threads. On a desktop, silence that isn't digitally silent (such as
background noise) is processed like speech.

## How the model was chosen

This first screen used 73 LibriSpeech dev-clean utterances (1,150 words, 8
minutes, `get-data.py` with no options). They all come from **one speaker**
(1272), so they compare engines but don't show how they do across speakers; use
the table above for that. "Noisy" adds white noise at 10 dB SNR.

| Engine | WER clean | WER noisy | Median lag | p90 lag | CPU | RAM |
|---|---|---|---|---|---|---|
| April English (Live Captions) | 12.5% | 22.4% | 0.40 s | 0.52 s | 0.29 | 907 MB |
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
model from the sherpa-onnx `asr-models` release. April is measured through the
`libaprilasr.so` that Live Captions builds; point `LIVECAPTIONS_DIR` at that
checkout.

```sh
python get-data.py --set test-clean        # also: --set test-other, or no option for the 73-clip set
python word_times.py --data data/test-clean
python bench.py sherpa --model ../models/nowcaption-fastconformer-en-80ms-cc-by-v1 \
    --name nowcaption --threads 1 --data data/test-clean
LIVECAPTIONS_DIR=~/LiveCaptions python bench.py april --name april-livecaptions --threads 1 --data data/test-clean
PYTHONPATH=../src python e2e.py --data data/test-clean     # needs pactl and paplay
```

Results are written to `results/`. Files named `*-dummy-*` are from the
single-speaker set.
