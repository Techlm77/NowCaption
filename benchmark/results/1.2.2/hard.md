# hard: speech model v4 against v3 (`compare.py --models v4 v3 --base v3`)

v4's results for these clips were run on the server (Ryzen 9 7900X, sherpa-onnx 1.13.8, the same model files and caption code) while other jobs ran. Its transcripts are identical to the PC's (checked on all 3,200 everyday clips for v4 and 12,592 clips for v3), so the word-error columns compare directly. The delay, CPU and memory columns depend on machine load and are not comparable here: use the PC tables. References are the text two NVIDIA models agreed on (Parakeet TDT 0.6B v2 and Canary-Qwen-2.5B); v4 was trained on Parakeet's transcripts, which may favour it here.

## Continuous use (2 models complete)

| Model | 4-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|
| v4 | 8.86% | -0.26 (-0.59 to +0.07) | — | — (no word timings) | — | — | 0 | 0.149 | 279 |
| v3 | 9.11% | — | — | — (no word timings) | — | — | 0 | 0.203 | 310 |

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).

Per set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:

| Model | gaming | names | numbers | streaming |
|---|---|---|---|---|
| v4 | -0.59 (-1.3…+0.1) | -0.42 (-1.1…+0.2) | -0.24 (-0.9…+0.4) | +0.21 (-0.5…+0.9) |

Speech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest

| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |
|---|---|---|---|---|---|
| v4 | 14.6% | 7.1% | — | — | 14.6% / — |
| v3 | 15.0% | 7.4% | — | — | 15.0% / — |

## Fresh stream per clip (0 models complete)

| Model | 4-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |
|---|---|---|---|---|---|---|---|---|---|

★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).
