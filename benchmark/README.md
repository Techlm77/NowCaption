# Benchmark

How NowCaption's model was chosen. All engines received the same audio in 100 ms
blocks, as live captioning does, with 1.5 s of silence after each clip.

- **Data:** 73 LibriSpeech dev-clean utterances, 1,150 words, 8 minutes
  (`get-data.py`). "Noisy" adds white noise at 10 dB SNR.
- **WER:** word error rate after lower-casing and removing punctuation
  ("mr" counts as "mister").
- **Lag:** the time a word first appears on screen, minus the time it ends in the
  audio. It includes compute time. Reference word end times come from offline
  Parakeet TDT 0.6B v2 timestamps (`word_times.py`) and are only used where that
  model heard the word correctly.
- **CPU:** process CPU seconds per second of audio, so 0.16 means 16% of one core.
- **Machine:** Ryzen 7 7800X3D, onnxruntime CPU.

| Engine | WER clean | WER noisy | Median lag | p90 lag | CPU | RAM |
|---|---|---|---|---|---|---|
| April English (Live Captions) | 12.5% | 22.4% | 0.40 s | 0.52 s | 0.29 | 907 MB |
| **FastConformer multi, 80 ms, CC-BY (NowCaption)** | **6.1%** | **10.2%** | **0.35 s** | **0.55 s** | **0.16** | **277 MB** |
| FastConformer 80 ms (NGC terms, not redistributable) | 6.3% | 9.9% | 0.41 s | 0.61 s | 0.16 | 276 MB |
| Nemotron Speech Streaming 0.6B, 560 ms | 4.7% | 8.4% | 0.61 s | 0.97 s | 0.25 | 1007 MB |
| Nemotron Speech Streaming 0.6B, 160 ms (2 threads) | 5.6% | 10.2% | 0.41 s | 0.67 s | 1.03 | 975 MB |

The table runs each clip from a fresh stream, which handicaps every engine
equally. On continuous audio (30 clips joined by 0.4 s gaps, `endpoint_test.py`),
NowCaption's model reaches **4.0%** WER. Resetting the stream at every pause
raises it to 8.4%, which is why the app never resets at pauses.

On real desktop audio, an early prototype with the same engine (and the NGC model)
ran side by side with Live Captions for 60 s. The prototype used 0.17 of a core and
352 MB; Live Captions used 0.42 of a core and 621 MB.

Screened and rejected:
- Parakeet unified 0.6B, 240 ms: slower than real time on 2 threads.
- Kroko streaming Zipformer: about 1 s of lag.
- Nemotron 3.5 multilingual, 320 ms: 7.1% WER on a 6-clip screen.

The April engine is measured through the `libaprilasr.so` that Live Captions
builds. Set the paths in `bench.py` to match your checkout.

```sh
python get-data.py
python word_times.py            # needs the parakeet-tdt-0.6b-v2-int8 sherpa model in ./models
python bench.py sherpa --model ../models/nowcaption-fastconformer-en-80ms-cc-by-v1 --name nowcaption --threads 1
```
