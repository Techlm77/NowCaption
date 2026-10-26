
## Continuous use (2 models complete)

| Model | 8-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v3 | 12.43% | -3.76 (-4.16 to -3.35) | 15.94% | — (no word timings) | — | — | 0 | 0.192 | 343 |
| v2 | 16.18% | — | 24.13% | — (no word timings) | — | — | 6 | 0.189 | 311 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | librispeech-clean | librispeech-other | common_voice | ami | voxpopuli | earnings22 | gigaspeech | urgent2024 |
|---|---|---|---|---|---|---|---|---|
| v3 | +0.12 (-0.2…+0.4) | -0.79 (-1.4…-0.2) | -8.19 (-9.7…-6.7) | -7.77 (-9.7…-6.0) | -0.75 (-1.2…-0.3) | -3.40 (-4.4…-2.3) | -2.59 (-3.5…-1.7) | -6.70 (-8.1…-5.3) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v3 | 16.3% | 9.5% | — | — | 18.6% / — |
| v2 | 26.9% | 11.0% | — | — | 36.1% / — |

## Fresh stream per clip (2 models complete)

| Model | 8-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v3 | 12.19% | -0.46 (-0.75 to -0.16) | 15.50% | — (no word timings) | — | — | 0 | 0.207 | 346 |
| v2 | 12.65% | — | 16.04% | — (no word timings) | — | — | 1 | 0.204 | 313 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | librispeech-clean | librispeech-other | common_voice | ami | voxpopuli | earnings22 | gigaspeech | urgent2024 |
|---|---|---|---|---|---|---|---|---|
| v3 | +0.28 (-0.1…+0.6) | -0.23 (-0.7…+0.3) | -0.54 (-1.5…+0.4) | -4.09 (-5.7…-2.5) | -0.02 (-0.4…+0.3) | +0.74 (-0.1…+1.6) | +0.15 (-0.5…+0.8) | +0.04 (-0.6…+0.7) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v3 | 16.0% | 9.4% | — | — | 17.5% / — |
| v2 | 16.8% | 8.7% | — | — | 17.6% / — |
