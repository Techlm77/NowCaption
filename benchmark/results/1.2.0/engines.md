## Offline engines on the same clips, continuous use

Continuous use, same clips, seed 0, 2.0-2.1 s gaps, 40 ms blocks, Ryzen 7 7800X3D. WER: Whisper English normaliser, plain mean of the 8 sets. Delays: on-screen ("shown"), per-set median / p90 averaged over the sets. CPU: process CPU seconds per second of audio, weighted by audio. RAM: peak resident memory of the test process. Engines dated 2026-10-25 ran side by side; 2026-10-26 runs ran alone.

| Engine | Measured | Words wrong (8 sets) | Shown: median / 90% | CPU (cores) | Peak RAM |
|---|---|---|---|---|---|
| NowCaption 1.2.0 (v3) | 2026-10-26 | 13.31% | 0.22 / 0.41 s | 0.191 | 348 MB |
| NowCaption 1.1.x (v2) | 2026-10-26 | 17.31% | 0.22 / 0.41 s | 0.187 | 319 MB |
| NowCaption 1.1.x (v2) | 2026-10-25 | 17.31% | 0.23 / 0.42 s | 0.240 | 314 MB |
| Moonshine Streaming Medium | 2026-10-25 | 13.64% | 0.91 / 1.47 s | 0.515 | 926 MB |
| Moonshine Streaming Small | 2026-10-25 | 15.23% | 0.80 / 1.22 s | 0.378 | 577 MB |
| Parakeet Realtime EOU 120M | 2026-10-25 | 16.46% | 0.15 / 0.39 s | 0.228 | 309 MB |
| Vosk en-us 0.22 (large) | 2026-10-25 | 28.46% | 0.56 / 1.13 s | 0.349 | 7,296 MB |
| Vosk small-en-us 0.15 | 2026-10-25 | 31.53% | 0.24 / 0.48 s | 0.063 | 523 MB |
| April english-dev-01110 (LiveCaptions) | 2026-10-25 | 32.37% | 0.29 / 0.43 s | 0.382 | 733 MB |

### Words wrong per set

| Engine | AMI | Common Voice | Earnings-22 | GigaSpeech | LS clean | LS other | URGENT | VoxPopuli |
|---|---|---|---|---|---|---|---|---|
| NowCaption 1.2.0 (v3) (2026-10-26) | 16.4% | 17.5% | 16.8% | 14.0% | 3.4% | 7.3% | 22.1% | 9.0% |
| NowCaption 1.1.x (v2) (2026-10-26) | 25.8% | 27.5% | 19.8% | 16.4% | 3.3% | 8.1% | 28.2% | 9.5% |
| NowCaption 1.1.x (v2) (2026-10-25) | 25.8% | 27.5% | 19.8% | 16.4% | 3.3% | 8.1% | 28.2% | 9.5% |
| Moonshine Streaming Medium (2026-10-25) | 16.4% | 23.0% | 15.7% | 10.5% | 2.8% | 6.7% | 23.8% | 10.0% |
| Moonshine Streaming Small (2026-10-25) | 19.0% | 24.2% | 16.9% | 11.5% | 3.3% | 9.2% | 26.7% | 11.1% |
| Parakeet Realtime EOU 120M (2026-10-25) | 23.4% | 26.6% | 17.3% | 14.4% | 3.9% | 9.1% | 26.9% | 10.1% |
| Vosk en-us 0.22 (large) (2026-10-25) | 34.8% | 38.4% | 38.0% | 20.1% | 8.1% | 21.1% | 49.5% | 17.5% |
| Vosk small-en-us 0.15 (2026-10-25) | 39.3% | 45.1% | 42.3% | 22.8% | 10.3% | 24.2% | 46.2% | 22.0% |
| April english-dev-01110 (LiveCaptions) (2026-10-25) | 33.4% | 57.4% | 37.9% | 20.1% | 10.8% | 23.1% | 45.3% | 30.9% |

### First 100 clips of each set (the only ones the live Whisper setup ran)

| Engine | Words wrong (8 sets) | Shown: median / 90% |
|---|---|---|
| NowCaption 1.2.0 (v3) (2026-10-26) | 13.43% | 0.21 / 0.40 s |
| NowCaption 1.1.x (v2) (2026-10-26) | 17.14% | 0.20 / 0.38 s |
| NowCaption 1.1.x (v2) (2026-10-25) | 17.14% | 0.21 / 0.40 s |
| Moonshine Streaming Medium (2026-10-25) | 14.68% | 0.90 / 1.49 s |
| Moonshine Streaming Small (2026-10-25) | 16.15% | 0.78 / 1.23 s |
| Parakeet Realtime EOU 120M (2026-10-25) | 16.28% | 0.14 / 0.37 s |
| Vosk en-us 0.22 (large) (2026-10-25) | 28.67% | 0.56 / 1.32 s |
| Vosk small-en-us 0.15 (2026-10-25) | 32.52% | 0.24 / 0.48 s |
| April english-dev-01110 (LiveCaptions) (2026-10-25) | 31.13% | 0.29 / 0.43 s |
| Whisper small.en, live (LocalAgreement-2) (2026-10-25) | 20.59% | 3.06 / 9.95 s |

Whisper live: 3.24 CPU cores (4 threads), peak 1,190 MB.
