
## Continuous use (2 models complete)

| Model | 20-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v3 ★ | 12.04% | -3.16 (-3.84 to -2.57) | — | 0.248 / 0.470 / 0.724 | +26 (+22 to +29) | 0.248 / 0.470 | 1 | 0.186 | 337 |
| v2 ★ | 15.19% | — | — | 0.217 / 0.426 / 0.692 | — | 0.217 / 0.426 | 4 | 0.184 | 309 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | africa | australia-nz | canada | england-midlands | england-north | england-south | india-south-asia | ireland | northern-ireland | other-l2 | scotland | uk-general | us-aae | us-general | us-hispanic | us-midwest | us-northeast | us-south | us-west | wales |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v3 | -2.78 (-4.5…-1.0) | -4.35 (-6.8…-2.1) | -6.69 (-9.3…-4.4) | -2.46 (-4.9…-0.3) | -1.56 (-2.6…-0.5) | -1.74 (-2.7…-0.7) | -6.70 (-8.4…-4.9) | -2.88 (-4.8…-1.1) | -1.92 (-6.7…+0.0) | -2.75 (-4.1…-1.4) | -2.72 (-3.8…-1.7) | -5.80 (-7.4…-4.2) | -1.50 (-2.8…-0.3) | -6.65 (-8.5…-5.0) | +5.00 (+0.0…+9.3) | -3.09 (-6.3…-0.8) | -2.45 (-7.5…+1.9) | -6.72 (-14.7…+0.0) | -2.96 (-5.3…-1.0) | -2.38 (-3.4…-1.4) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v3 | 15.9% | 12.0% | 0.340 s | 0.233 s | 15.9% / 0.340 s |
| v2 | 26.1% | 13.8% | 0.299 s | 0.205 s | 26.1% / 0.299 s |

## Fresh stream per clip (2 models complete)

| Model | 20-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v3 ★ | 12.07% | -0.16 (-0.61 to +0.27) | — | 0.289 / 0.516 / 0.754 | -79 (-81 to -76) | 0.289 / 0.516 | 0 | 0.206 | 341 |
| v2 | 12.23% | — | — | 0.367 / 0.586 / 0.846 | — | 0.367 / 0.586 | 3 | 0.204 | 309 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | africa | australia-nz | canada | england-midlands | england-north | england-south | india-south-asia | ireland | northern-ireland | other-l2 | scotland | uk-general | us-aae | us-general | us-hispanic | us-midwest | us-northeast | us-south | us-west | wales |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v3 | -0.20 (-1.9…+1.4) | +0.40 (-0.9…+1.7) | +0.30 (-0.8…+1.5) | -1.31 (-3.5…+0.8) | +0.04 (-0.8…+0.9) | +0.32 (-0.4…+1.1) | -0.73 (-1.9…+0.5) | +1.07 (-0.7…+2.7) | +0.00 (+0.0…+0.0) | -0.11 (-1.2…+1.0) | -0.43 (-1.6…+0.7) | -1.01 (-2.2…+0.1) | +0.28 (-0.9…+1.5) | +0.51 (-0.5…+1.5) | +0.00 (+0.0…+0.0) | +0.00 (-1.0…+1.2) | +2.45 (-2.2…+6.7) | -3.36 (-8.9…+1.8) | -0.81 (-1.9…+0.0) | -0.66 (-1.9…+0.5) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v3 | 17.1% | 12.4% | 0.383 s | 0.274 s | 17.1% / 0.383 s |
| v2 | 17.5% | 11.9% | 0.461 s | 0.352 s | 17.5% / 0.461 s |
