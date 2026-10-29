# esb-fresh: speech model v4 against v3 (`compare.py --models v4 v3 --base v3`)

v4's results for these clips were run on the server (Ryzen 9 7900X, sherpa-onnx 1.13.8, the same model files and caption code) while other jobs ran. Its transcripts are identical to the PC's (checked on all 3,200 everyday clips for v4 and 12,592 clips for v3), so the word-error columns compare directly. The delay, CPU and memory columns depend on machine load and are not comparable here: use the PC tables.

## Continuous use (2 models complete)

| Model | 8-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v4 | 12.33% | -0.10 (-0.30 to +0.11) | 15.68% | — (no word timings) | — | — | 1 | 0.141 | 287 |
| v3 | 12.43% | — | 15.94% | — (no word timings) | — | — | 0 | 0.192 | 343 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | librispeech-clean | librispeech-other | common_voice | ami | voxpopuli | earnings22 | gigaspeech | urgent2024 |
|---|---|---|---|---|---|---|---|---|
| v4 | -0.16 (-0.4…+0.0) | -0.19 (-0.5…+0.2) | -0.26 (-1.2…+0.5) | +0.22 (-0.7…+1.1) | +0.05 (-0.2…+0.4) | +0.16 (-0.4…+0.7) | -0.09 (-0.5…+0.4) | -0.50 (-1.1…+0.1) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v4 | 15.8% | 9.4% | — | — | 17.9% / — |
| v3 | 16.3% | 9.5% | — | — | 18.6% / — |

## Fresh stream per clip (2 models complete)

| Model | 8-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v4 | 11.94% | -0.26 (-0.47 to -0.06) | 15.30% | — (no word timings) | — | — | 0 | 0.121 | 283 |
| v3 | 12.19% | — | 15.50% | — (no word timings) | — | — | 0 | 0.207 | 346 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | librispeech-clean | librispeech-other | common_voice | ami | voxpopuli | earnings22 | gigaspeech | urgent2024 |
|---|---|---|---|---|---|---|---|---|
| v4 | -0.01 (-0.2…+0.1) | +0.00 (-0.3…+0.3) | -0.21 (-0.9…+0.5) | -0.67 (-1.6…+0.2) | -0.04 (-0.3…+0.2) | -0.56 (-1.2…+0.0) | -0.14 (-0.5…+0.3) | -0.41 (-1.0…+0.1) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v4 | 15.9% | 9.2% | — | — | 17.0% / — |
| v3 | 16.0% | 9.4% | — | — | 17.5% / — |
