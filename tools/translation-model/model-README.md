# NowCaption Translate v1 (33 languages to English, CTranslate2 int8)

The text translation model behind NowCaption's optional **Translate to English**.
It turns text in any of 33 languages into English and does not need to be told
which language it is reading.

- Languages: Arabic, Bulgarian, Chinese, Croatian, Czech, Danish, Dutch, Estonian,
  Finnish, French, German, Greek, Hebrew, Hindi, Hungarian, Italian, Japanese,
  Korean, Latvian, Lithuanian, Norwegian, Polish, Portuguese, Romanian, Russian,
  Slovak, Slovenian, Spanish, Swedish, Thai, Turkish, Ukrainian, Vietnamese.
- Architecture: Transformer, 12 encoder layers and 2 decoder layers, width 768,
  12 heads; 64k-piece SentencePiece vocabulary for the source, 16k for English.
  The decoder is shallow because it runs once per output word.
- Trained from scratch by NowCaption (`tools/translation-model` in the NowCaption
  repository) on public parallel text from [OPUS](https://opus.nlpl.eu):
  OpenSubtitles, CCMatrix, Europarl, WikiMatrix, Tatoeba, GlobalVoices,
  News-Commentary, SETIMES, UNPC, ParaCrawl and SCB-MT-EN-TH, plus English written
  for part of that text by [MADLAD-400 3B](https://huggingface.co/google/madlad400-3b-mt)
  (Apache-2.0) as a teacher. Sentences of the FLEURS benchmark were removed from the
  training data.

## Results

Against M2M100 418M, the model NowCaption used before, both as CTranslate2 int8 on a
Ryzen 7 7800X3D (one sentence at a time, 2 threads). chrF: higher is better.

| | M2M100 418M | NowCaption Translate v1 |
|---|---|---|
| FLEURS test, 33 languages (10,908 sentences) | 56.52 | **58.37** (ahead in 32) |
| Speech: Nemotron 3.5 transcripts of FLEURS audio (1,320 clips) | 45.87 | **47.41** (ahead in 31) |
| Time per sentence (mean / slowest) | 525 / 1,437 ms | **28 / 66 ms** |
| Size / memory while translating | 496 / 599 MB | **172 / 214 MB** |
| Load time | 1.06 s | **0.11 s** |

Its weakest language against M2M100 is Vietnamese (−0.4 on text, −1.1 on speech).

## Files

`model.bin`, `config.json`, `source_vocabulary.json`, `target_vocabulary.json`:
the CTranslate2 model. `source.spm` and `target.spm`: SentencePiece models.

```python
import ctranslate2, sentencepiece as spm
m = ctranslate2.Translator('nowcaption-translate-v1', compute_type='int8')
src = spm.SentencePieceProcessor(model_file='nowcaption-translate-v1/source.spm')
tgt = spm.SentencePieceProcessor(model_file='nowcaption-translate-v1/target.spm')
out = m.translate_batch([src.encode('¿Dónde está la estación?', out_type=str)], beam_size=1)
print(tgt.decode_pieces(out[0].hypotheses[0]))
```

## License

CC-BY-4.0 (see LICENSE), like the OPUS-MT models trained on the same collection.
The training corpora keep their own terms; see the OPUS pages of each corpus.
