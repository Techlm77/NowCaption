### headline

| | v3 (1.2.0, 1.2.1) | **v4 (1.2.2)** | Change, with its 95% range |
|---|---|---|---|
| Words wrong, continuous use | 13.31% | **13.04%** | −0.27 (−0.47 to −0.07) points |
| Words wrong, fresh stream per clip | 13.18% | **12.89%** | −0.28 (−0.49 to −0.08) |
| The same, on 400 other clips per set never used to choose the model: continuous / fresh | 12.43% / 12.19% | **12.33% / 11.94%** | −0.10 (−0.30 to +0.11) / −0.26 (−0.47 to −0.06) |
| Accent test set (2,992 clips, 20 regions), continuous use: all clips / average of the regions | 13.78% / 12.04% | **13.47% / 11.53%** | average −0.51 (−0.85 to −0.11) |
| Hard cases (370 YouTube clips: gaming, names, numbers, live streams), continuous use: all clips / average of the 4 kinds | 9.07% / 9.11% | **8.82% / 8.86%** | average −0.26 (−0.59 to +0.07) |
| First two words after a pause, continuous use | 17.4% wrong | **16.9%** wrong | |
| Time until a word shows, continuous use: median / 90% / 95% / 99% | 0.210 / 0.406 / 0.472 / 0.619 s | **0.204 / 0.403 / 0.468** / 0.620 s | median −6 (−8 to −6) ms, 90% −4 (−6 to −3) ms, on the words both got right |
| Time until a word shows, fresh stream: median / 90% / 95% / 99% | 0.235 / 0.448 / 0.513 / 0.658 s | **0.226 / 0.435 / 0.501 / 0.646 s** | median −9 (−10 to −8) ms |

### esb-sets

| Test set | v3, continuous | **v4, continuous** | Change (95% range) | v3, fresh | **v4, fresh** | Change (95% range) |
|---|---|---|---|---|---|---|
| AMI (meetings) | 16.4% | **15.5%** | −0.90 (−1.74 to −0.06) | 16.5% | **15.2%** | −1.36 (−2.29 to −0.48) |
| Common Voice (cheap microphones, accents) | 17.5% | **17.5%** | +0.00 (−0.81 to +0.86) | 17.2% | **17.5%** | +0.36 (−0.48 to +1.20) |
| URGENT 2024 (noisy speech) | 22.1% | **21.5%** | −0.66 (−1.33 to −0.03) | 21.6% | **21.0%** | −0.62 (−1.26 to +0.03) |
| Earnings-22 (business calls, accents) | 16.8% | **16.3%** | −0.51 (−1.09 to +0.04) | 16.6% | **16.5%** | −0.07 (−0.62 to +0.46) |
| GigaSpeech (podcasts, YouTube) | 14.0% | **14.0%** | −0.01 (−0.47 to +0.48) | 13.6% | **13.6%** | +0.05 (−0.37 to +0.46) |
| VoxPopuli (speeches, accents) | 9.0% | **8.9%** | −0.06 (−0.32 to +0.22) | 9.2% | **8.9%** | −0.27 (−0.58 to +0.02) |
| LibriSpeech test-other | 7.3% | **7.4%** | +0.09 (−0.29 to +0.51) | 7.5% | **7.4%** | −0.11 (−0.49 to +0.28) |
| LibriSpeech test-clean | 3.4% | **3.3%** | −0.13 (−0.38 to +0.13) | 3.3% | **3.1%** | −0.24 (−0.46 to −0.01) |
| **Average** | **13.31%** | **13.04%** | **−0.27 (−0.47 to −0.07)** | **13.18%** | **12.89%** | **−0.28 (−0.49 to −0.08)** |

### sdi

| | Words | Substituted | Deleted (missed) | Inserted (extra) | Words wrong |
|---|---|---|---|---|---|
| 8 everyday sets, continuous, v3 | 54,033 | 3,590 (6.64%) | 2,234 (4.13%) | 904 (1.67%) | 12.45% |
| 8 everyday sets, continuous, v4 | 54,033 | 3,535 (6.54%) | 2,194 (4.06%) | 879 (1.63%) | 12.23% |
| 8 everyday sets, fresh stream, v3 | 54,033 | 3,537 (6.55%) | 2,263 (4.19%) | 858 (1.59%) | 12.32% |
| 8 everyday sets, fresh stream, v4 | 54,033 | 3,520 (6.51%) | 2,161 (4.00%) | 851 (1.57%) | 12.09% |
| 400 other clips per set, continuous, v3 | 54,279 | 3,368 (6.20%) | 2,205 (4.06%) | 728 (1.34%) | 11.61% |
| 400 other clips per set, continuous, v4 | 54,279 | 3,378 (6.22%) | 2,123 (3.91%) | 741 (1.37%) | 11.50% |
| 400 other clips per set, fresh, v3 | 54,279 | 3,360 (6.19%) | 2,141 (3.94%) | 730 (1.34%) | 11.48% |
| 400 other clips per set, fresh, v4 | 54,279 | 3,294 (6.07%) | 2,102 (3.87%) | 720 (1.33%) | 11.27% |
| Accents, continuous, v3 | 37,310 | 3,101 (8.31%) | 1,618 (4.34%) | 421 (1.13%) | 13.78% |
| Accents, continuous, v4 | 37,310 | 3,062 (8.21%) | 1,550 (4.15%) | 412 (1.10%) | 13.47% |
| Hard cases, continuous, v3 | 13,610 | 723 (5.31%) | 335 (2.46%) | 177 (1.30%) | 9.07% |
| Hard cases, continuous, v4 | 13,610 | 704 (5.17%) | 315 (2.31%) | 182 (1.34%) | 8.82% |

### other-clips-sets

| Test set (400 other clips) | v3, continuous | **v4, continuous** | Change (95% range) | v3, fresh | **v4, fresh** | Change (95% range) |
|---|---|---|---|---|---|---|
| AMI (meetings) | 15.9% | **16.1%** | +0.22 (−0.68 to +1.11) | 14.8% | **14.2%** | −0.67 (−1.57 to +0.18) |
| Common Voice (cheap microphones, accents) | 15.9% | **15.7%** | −0.26 (−1.17 to +0.53) | 15.5% | **15.3%** | −0.21 (−0.93 to +0.49) |
| URGENT 2024 (noisy speech) | 20.3% | **19.8%** | −0.50 (−1.08 to +0.05) | 19.8% | **19.4%** | −0.41 (−0.99 to +0.15) |
| Earnings-22 (business calls, accents) | 15.7% | **15.9%** | +0.16 (−0.43 to +0.74) | 16.2% | **15.6%** | −0.56 (−1.25 to +0.05) |
| GigaSpeech (podcasts, YouTube) | 13.1% | **13.0%** | −0.09 (−0.54 to +0.35) | 13.0% | **12.9%** | −0.14 (−0.55 to +0.26) |
| VoxPopuli (speeches, accents) | 8.2% | **8.3%** | +0.05 (−0.23 to +0.36) | 8.1% | **8.1%** | −0.04 (−0.29 to +0.20) |
| LibriSpeech test-other | 7.1% | **6.9%** | −0.19 (−0.54 to +0.17) | 6.8% | **6.8%** | +0.00 (−0.32 to +0.32) |
| LibriSpeech test-clean | 3.1% | **2.9%** | −0.16 (−0.37 to +0.04) | 3.2% | **3.2%** | −0.01 (−0.16 to +0.15) |
| **Average** | **12.43%** | **12.33%** | **−0.10 (−0.30 to +0.11)** | **12.19%** | **11.94%** | **−0.26 (−0.47 to −0.06)** |

### accents

| Region | Clips | v3 | **v4** | Change (95% range) | |
|---|---|---|---|---|---|
| Africa | 159 | 23.0% | **22.8%** | −0.24 (−1.20 to +0.75) | within chance |
| Australia and New Zealand | 122 | 9.5% | **8.9%** | −0.55 (−1.77 to +0.62) | within chance |
| Canada | 102 | 7.6% | **7.3%** | −0.30 (−1.05 to +0.32) | within chance |
| England, Midlands | 44 | 6.2% | **6.2%** | +0.00 (−0.99 to +1.07) | within chance |
| England, North | 171 | 6.0% | **6.5%** | +0.43 (−0.17 to +1.05) | within chance |
| England, South | 300 | 10.4% | **9.8%** | −0.54 (−1.42 to +0.20) | within chance |
| India and South Asia | 300 | 17.7% | **16.9%** | −0.83 (−1.73 to +0.03) | within chance |
| Ireland | 106 | 20.2% | **20.2%** | −0.05 (−1.05 to +0.93) | within chance |
| Northern Ireland | 5 | 1.9% | **3.9%** | +1.92 (+0.00 to +5.66) | within chance |
| Other second-language English | 300 | 17.7% | **17.0%** | −0.71 (−1.66 to +0.18) | within chance |
| Scotland | 213 | 10.6% | **10.7%** | +0.13 (−0.62 to +0.90) | within chance |
| UK general | 300 | 13.0% | **13.0%** | −0.03 (−0.88 to +0.79) | within chance |
| US African-American English | 300 | 19.9% | **19.4%** | −0.50 (−1.35 to +0.36) | within chance |
| US Hispanic | 5 | 15.0% | **10.0%** | −5.00 (−9.30 to +0.00) | within chance |
| US Midwest | 35 | 14.2% | **14.2%** | +0.00 (−0.81 to +0.83) | within chance |
| US Northeast | 15 | 11.0% | **9.2%** | −1.84 (−4.57 to +0.00) | within chance |
| US South | 12 | 12.6% | **10.9%** | −1.68 (−4.50 to +0.00) | within chance |
| US West | 29 | 4.8% | **4.8%** | +0.00 (−0.78 to +0.84) | within chance |
| US general | 300 | 12.1% | **11.9%** | −0.26 (−0.97 to +0.45) | within chance |
| Wales | 174 | 7.1% | **6.9%** | −0.13 (−0.74 to +0.44) | within chance |
| **All 2,992 clips** | 2,992 | **13.78%** | **13.47%** | average of the regions −0.51 (−0.85 to −0.11) | |

Accent regions: 14 of 20 lower, 0 better beyond chance, 0 worse beyond chance.

Accents, fresh stream, all clips: v3 14.10% → v4 14.06%.

### hard

| Kind | Clips | v3 | **v4** | Change (95% range) |
|---|---|---|---|---|
| Gaming (game titles, gameplay talk) | 100 | 8.8% | **8.2%** | −0.59 (−1.26 to +0.10) |
| Names (people, places, products) | 90 | 8.6% | **8.2%** | −0.42 (−1.07 to +0.20) |
| Numbers (prices, times, years, scores) | 90 | 10.2% | **10.0%** | −0.24 (−0.91 to +0.40) |
| Live streams (casual talk) | 90 | 8.8% | **9.0%** | +0.21 (−0.45 to +0.86) |
| **All 370 clips** | 370 | **9.07%** | **8.82%** | average of the kinds −0.26 (−0.59 to +0.07) |

### nonspeech

| | Clips | v3: words printed | **v4: words printed** | Clips with any word (v3 / v4) |
|---|---|---|---|---|
| MUSAN noise, 10 s each | 60 | 0 | **0** | 0 / 0 |
| MUSAN instrumental music, 10 s each | 40 | 35 | **27** | 2 / 2 (0036.wav, 0039.wav) |

### engines-row

| **NowCaption 1.2.2** | This app; speech model v4, CC-BY-4.0 | **13.04%** | 0.215 / 0.411 s | 0.141 | 333 MB |
| NowCaption 1.2.0 | Speech model v3 | 13.31% | 0.221 / 0.416 s | 0.191 | 348 MB |

### bench

| Engine | WER test-clean | WER test-other | Median lag | p90 lag | CPU | RAM |
|---|---|---|---|---|---|---|
| NowCaption 1.2.0 (speech model v3) | 2.7% | 6.9% | 0.15–0.23 s | 0.31–0.39 s | 0.15 | 307–311 MB |
| **NowCaption 1.2.2 (speech model v4)** | 2.7% | 6.7% | 0.13–0.21 s | 0.29–0.37 s | 0.06 | 291–296 MB |

raw: {"bench_test-clean": {"wer": 0.027, "errors": 85, "words": 3151, "first_shown_lag_s_p50": 0.13, "first_shown_lag_s_p90": 0.29, "step_ms_p99": 10.201, "cpu_rtf": 0.0592, "peak_rss_mb": 296}, "bench_v3_test-clean": {"wer": 0.0267, "errors": 84, "words": 3151, "first_shown_lag_s_p50": 0.146, "first_shown_lag_s_p90": 0.306, "step_ms_p99": 26.498, "cpu_rtf": 0.1544, "peak_rss_mb": 307}, "bench_test-other": {"wer": 0.0671, "errors": 159, "words": 2369, "first_shown_lag_s_p50": 0.21, "first_shown_lag_s_p90": 0.37, "step_ms_p99": 10.095, "cpu_rtf": 0.06, "peak_rss_mb": 291}, "bench_v3_test-other": {"wer": 0.0688, "errors": 163, "words": 2369, "first_shown_lag_s_p50": 0.226, "first_shown_lag_s_p90": 0.389, "step_ms_p99": 27.725, "cpu_rtf": 0.1544, "peak_rss_mb": 311}}

### pipeline

| | Runs | WER | Mean lag | Median lag | p90 lag | CPU (recognition thread) |
|---|---|---|---|---|---|---|
| test-clean, v3 | 3 | 2.74% | 0.137 s | 0.162 s | 0.315 s | 0.174 |
| **test-clean, v4** | 3 | **2.54%** | 0.121 s | 0.147 s | 0.299 s | 0.073 |
| test-other, v3 | 6 | 6.42% | 0.186 s | 0.211 s | 0.380 s | 0.170 |
| **test-other, v4** | 6 | **6.70%** | 0.170 s | 0.195 s | 0.363 s | 0.072 |

### stages

| Stage (ms of CPU per second of audio) | v3 test-clean / test-other | **v4 test-clean / test-other** |
|---|---|---|
| speech model (decode_stream: encoder and greedy search) | 155.6 / 154.8 | **57.8 / 57.5** |
| punctuation and casing | 11.6 / 11.0 | **11.2 / 10.9** |
| reading the recognised text (get_result) | 2.4 / 1.7 | **2.2 / 1.6** |
| audio features (accept_waveform) | 1.1 / 1.1 | **1.1 / 1.1** |
| is_ready | 0.1 / 0.1 | **0.0 / 0.1** |
| new stream | 0.0 / 0.0 | **0.0 / 0.0** |
| everything else (Python) | 0.5 / 0.4 | **0.4 / 0.4** |

### e2e

| | test-clean, v3 | **test-clean, v4** | test-other, v3 | **test-other, v4** |
|---|---|---|---|---|
| Median lag | 0.201 s | **0.192 s** | 0.240 s | **0.226 s** |
| p90 lag | 0.364 s | **0.353 s** | 0.402 s | **0.385 s** |
| p95 lag | — | **0.400 s** | — | **0.437 s** |
| p99 lag | 0.534 s | **0.491 s** | 0.588 s | **0.586 s** |
| First word of each clip: median / 90% lag | — | **0.320 / 0.471 s** | — | **0.356 / 0.586 s** |
| Timed words recognised correctly | 678 of 687 (98.7%) | **680 of 687 (99.0%)** | 574 of 598 (96.0%) | **579 of 598 (96.8%)** |
| CPU while a clip plays | 0.195 core | **0.093 core** | 0.191 core | **0.093 core** |
| CPU in the 3 s after each clip | 0.110 core | **0.048 core** | 0.108 core | **0.048 core** |
| CPU during digital silence | 0.008 core | **0.005 core** | 0.008 core | **0.005 core** |
| Peak memory | 353 MB | **336 MB** | 350 MB | **334 MB** |
| Audio blocks fed / most waiting at once / dropped | — (not recorded) | **10,087 / 1 / 0 s** | — (not recorded) | **8,415 / 1 / 0 s** |

