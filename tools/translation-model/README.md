# Translation model

How NowCaption's 33-languages-to-English model (`nowcaption-translate-v1`) was made.
Run everything from an empty work folder; data and checkpoints go there. A GPU with
16 GB is enough (trained on a Radeon RX 7800 XT with PyTorch for ROCm in about 14 hours).

```sh
pip install torch sentencepiece ctranslate2==4.8.3 sacrebleu fasttext-numpy2-wheel langid numpy
T=path/to/NowCaption/tools/translation-model
curl -fLo data/lid.176.ftz --create-dirs https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz
python3 $T/opus_catalog.py                  # what OPUS has for each language
python3 $T/fleurs_text.py                   # FLEURS dev/test text, for scoring
python3 $T/build_data.py ar bg cs da de el es et fi fr he hi hr hu it ja ko lt lv nl no pl pt ro ru sk sl sv th tr uk vi zh
python3 $T/prepare.py spm && python3 $T/prepare.py encode
python3 $T/train.py runs/v1 --steps 66000
# Distillation, while it trains: MADLAD-400 3B (Apache-2.0) writes English for a sample of
# the training sources, the pairs are filtered, and training resumes on data that has them.
ct2-transformers-converter --model google/madlad400-3b-mt --output_dir madlad-3b --quantization int8
python3 $T/distill.py madlad-3b N cuda 4 ar bg cs ...   # N sentences per language; distill_gpu.py is faster on CUDA
python3 $T/kd_filter.py ar bg cs ...
python3 $T/prepare.py encode                # adds data/kd/*.tsv, KD_REPEATS times over
python3 $T/train.py runs/v1 --steps 66000 --resume --new-data
python3 $T/convert.py runs/v1/ck066000.pt nowcaption-translate-v1
$T/pack.sh nowcaption-translate-v1          # dist/nowcaption-translate-v1.tar.bz2
```

`build_data.py` exits with a nonzero status if a corpus fails or a requested language has
no usable pairs. Its `data/clean/manifest.json` records the requested language set before
downloads start. Retry just the failed languages with the same command; earlier requests
remain in the manifest. `prepare.py` refuses incomplete or missing languages, and
`train.py` checks that the encoded data contains every requested language. Older work
folders without a manifest must contain all 33 languages; run `build_data.py` with an
explicit subset to record a deliberately smaller training set.

| File | Does |
|---|---|
| `build_data.py` | Samples OPUS corpora per language and filters them (length, script, fastText language ID, duplicates, FLEURS sentences) |
| `prepare.py` | SentencePiece models; encodes the pairs, adding live-caption variants (no final punctuation, no punctuation or capitals) |
| `model.py` | The Transformer, laid out like CTranslate2's so `convert.py` copies weights one to one |
| `train.py` | Training with FLEURS dev scores every 2,500 steps |
| `distill.py`, `distill_gpu.py`, `kd_filter.py` | Teacher translations by MADLAD-400 3B, and the same filters as the training data plus repetition loops |
| `average.py` | Averages checkpoints (for v1 the last checkpoint alone scored higher: 59.71 against 59.61) |
| `eval_ct2.py`, `compare.py` | chrF on FLEURS, and speed, memory and runaway output against the old M2M100 |
| `test_convert.py` | Checks that CTranslate2 and PyTorch give the same scores |

v1 added the teacher data in four rounds at checkpoints between 25,000 and 55,000 steps,
as the teacher finished each round on a second GPU. Results are in `model-README.md`,
which `pack.sh` puts in the release as the model's README.

Known limitation of v1: it learned from whole sentences, but live captions translate
sentences while they are still being spoken. NowCaption shows only the English two
translations agree on and translates each finished sentence again whole, but the model
itself has not learned to translate an unfinished sentence without guessing its end. The
next version should also train on cut-off pairs: a sentence's start with the teacher's
translation of just that start.
