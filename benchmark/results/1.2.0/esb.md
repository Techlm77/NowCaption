
## Continuous use (2 models complete)

| Model | 8-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v3 ★ | 13.31% | -4.01 (-4.45 to -3.59) | 17.49% | 0.210 / 0.406 / 0.619 | -13 (-15 to -10) | 0.210 / 0.406 | 0 | 0.191 | 348 |
| v2 | 17.31% | — | 27.51% | 0.219 / 0.399 / 0.591 | — | 0.219 / 0.399 | 6 | 0.186 | 319 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | librispeech-clean | librispeech-other | common_voice | ami | voxpopuli | earnings22 | gigaspeech | urgent2024 |
|---|---|---|---|---|---|---|---|---|
| v3 | +0.06 (-0.4…+0.5) | -0.73 (-1.3…-0.2) | -10.02 (-11.9…-8.3) | -9.35 (-11.2…-7.5) | -0.52 (-1.0…-0.1) | -2.99 (-4.1…-1.9) | -2.45 (-3.5…-1.5) | -6.05 (-7.5…-4.7) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v3 | 17.4% | 9.9% | 0.285 s | 0.203 s | 19.0% / 0.404 s |
| v2 | 28.4% | 11.7% | 0.283 s | 0.214 s | 41.1% / 0.378 s |

## Fresh stream per clip (2 models complete)

| Model | 8-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v3 ★ | 13.18% | -0.34 (-0.65 to -0.03) | 17.18% | 0.235 / 0.448 / 0.658 | -82 (-84 to -80) | 0.235 / 0.448 | 0 | 0.204 | 351 |
| v2 | 13.51% | — | 18.26% | 0.317 / 0.528 / 0.736 | — | 0.317 / 0.528 | 5 | 0.206 | 316 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | librispeech-clean | librispeech-other | common_voice | ami | voxpopuli | earnings22 | gigaspeech | urgent2024 |
|---|---|---|---|---|---|---|---|---|
| v3 | +0.38 (+0.1…+0.7) | +0.46 (-0.1…+1.0) | -1.08 (-2.4…+0.2) | -3.23 (-4.7…-1.9) | +0.19 (-0.2…+0.6) | -0.54 (-1.5…+0.4) | +0.21 (-0.3…+0.8) | +0.88 (+0.0…+1.7) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v3 | 17.0% | 9.9% | 0.319 s | 0.226 s | 17.6% / 0.419 s |
| v2 | 17.4% | 9.3% | 0.367 s | 0.311 s | 18.1% / 0.519 s |
