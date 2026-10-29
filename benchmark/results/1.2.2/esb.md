# esb: speech model v4 against v3 (`compare.py --models v4 v3 --base v3`)

## Continuous use (2 models complete)

| Model | 8-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v4 ★ | 13.04% | -0.27 (-0.47 to -0.07) | 17.49% | 0.204 / 0.403 / 0.620 | -6 (-8 to -6) | 0.204 / 0.403 | 1 | 0.141 | 333 |
| v3 | 13.31% | — | 17.49% | 0.210 / 0.406 / 0.619 | — | 0.210 / 0.406 | 0 | 0.191 | 348 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | librispeech-clean | librispeech-other | common_voice | ami | voxpopuli | earnings22 | gigaspeech | urgent2024 |
|---|---|---|---|---|---|---|---|---|
| v4 | -0.13 (-0.4…+0.1) | +0.09 (-0.3…+0.5) | +0.00 (-0.8…+0.9) | -0.90 (-1.7…-0.1) | -0.06 (-0.3…+0.2) | -0.51 (-1.1…+0.0) | -0.01 (-0.5…+0.5) | -0.66 (-1.3…-0.0) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v4 | 16.9% | 9.8% | 0.284 s | 0.196 s | 18.0% / 0.421 s |
| v3 | 17.4% | 9.9% | 0.285 s | 0.203 s | 19.0% / 0.404 s |

## Fresh stream per clip (2 models complete)

| Model | 8-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v4 ★ | 12.89% | -0.28 (-0.49 to -0.08) | 17.54% | 0.226 / 0.435 / 0.646 | -9 (-10 to -8) | 0.226 / 0.435 | 0 | 0.163 | 336 |
| v3 | 13.18% | — | 17.18% | 0.235 / 0.448 / 0.658 | — | 0.235 / 0.448 | 0 | 0.204 | 351 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | librispeech-clean | librispeech-other | common_voice | ami | voxpopuli | earnings22 | gigaspeech | urgent2024 |
|---|---|---|---|---|---|---|---|---|
| v4 | -0.24 (-0.5…-0.0) | -0.11 (-0.5…+0.3) | +0.36 (-0.5…+1.2) | -1.36 (-2.3…-0.5) | -0.27 (-0.6…+0.0) | -0.07 (-0.6…+0.5) | +0.05 (-0.4…+0.5) | -0.62 (-1.3…+0.0) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v4 | 16.4% | 9.7% | 0.310 s | 0.217 s | 17.9% / 0.402 s |
| v3 | 17.0% | 9.9% | 0.319 s | 0.226 s | 17.6% / 0.419 s |
