# accents: speech model v4 against v3 (`compare.py --models v4 v3 --base v3`)

Both models on the 7800X3D, 8 jobs at once (v3 on 26 October, v4 on 29 October 2026). v4's continuous-use transcripts are identical to an earlier run on the server.

## Continuous use (2 models complete)

| Model | 20-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v4 ★ | 11.53% | -0.51 (-0.85 to -0.11) | — | 0.242 / 0.466 / 0.732 | -7 (-7 to -5) | 0.242 / 0.466 | 2 | 0.143 | 325 |
| v3 | 12.04% | — | — | 0.248 / 0.470 / 0.724 | — | 0.248 / 0.470 | 1 | 0.186 | 337 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | africa | australia-nz | canada | england-midlands | england-north | england-south | india-south-asia | ireland | northern-ireland | other-l2 | scotland | uk-general | us-aae | us-general | us-hispanic | us-midwest | us-northeast | us-south | us-west | wales |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v4 | -0.24 (-1.2…+0.7) | -0.55 (-1.8…+0.6) | -0.30 (-1.0…+0.3) | +0.00 (-1.0…+1.1) | +0.43 (-0.2…+1.0) | -0.54 (-1.4…+0.2) | -0.83 (-1.7…+0.0) | -0.05 (-1.0…+0.9) | +1.92 (+0.0…+5.7) | -0.71 (-1.7…+0.2) | +0.13 (-0.6…+0.9) | -0.03 (-0.9…+0.8) | -0.50 (-1.4…+0.4) | -0.26 (-1.0…+0.5) | -5.00 (-9.3…+0.0) | +0.00 (-0.8…+0.8) | -1.84 (-4.6…+0.0) | -1.68 (-4.5…+0.0) | +0.00 (-0.8…+0.8) | -0.13 (-0.7…+0.4) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v4 | 15.4% | 11.8% | 0.345 s | 0.225 s | 15.4% / 0.345 s |
| v3 | 15.9% | 12.0% | 0.340 s | 0.233 s | 15.9% / 0.340 s |

## Fresh stream per clip (2 models complete)

| Model | 20-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v3 ★ | 12.07% | — | — | 0.289 / 0.516 / 0.754 | — | 0.289 / 0.516 | 0 | 0.206 | 341 |
| v4 ★ | 12.45% | +0.38 (-0.09 to +0.93) | — | 0.273 / 0.498 / 0.743 | -17 (-19 to -15) | 0.273 / 0.498 | 0 | 0.158 | 327 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | africa | australia-nz | canada | england-midlands | england-north | england-south | india-south-asia | ireland | northern-ireland | other-l2 | scotland | uk-general | us-aae | us-general | us-hispanic | us-midwest | us-northeast | us-south | us-west | wales |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v4 | +0.48 (-1.0…+2.0) | +0.00 (-1.1…+1.1) | +0.20 (-0.9…+1.4) | +0.33 (-0.3…+1.2) | +0.13 (-0.5…+0.8) | +0.14 (-0.4…+0.8) | -0.96 (-1.8…-0.2) | -0.21 (-1.4…+0.9) | +3.85 (+0.0…+7.5) | +0.25 (-0.8…+1.4) | -0.16 (-1.2…+0.8) | -0.44 (-1.2…+0.3) | -0.33 (-1.1…+0.5) | -0.03 (-0.7…+0.6) | +0.00 (-6.1…+7.9) | +2.06 (+0.6…+3.8) ✗ | -1.23 (-4.2…+1.3) | +3.36 (+0.0…+7.8) | +0.27 (+0.0…+0.9) | -0.09 (-0.7…+0.5) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v3 | 17.1% | 12.4% | 0.383 s | 0.274 s | 17.1% / 0.383 s |
| v4 | 17.0% | 12.3% | 0.369 s | 0.257 s | 17.0% / 0.369 s |
