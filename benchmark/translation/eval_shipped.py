"""NowCaption 1.2.0's translation: Nemotron 3.5 transcripts (eval_nemotron.py) ->
langid.py -> M2M100 418M CTranslate2 int8, scored with chrF; also how often langid.py
picks the right language, and what a wrong source-language tag costs.
Usage: eval_shipped.py <nemotron-asr.json> <m2m100-ct2-int8-dir>   (the dir must hold
sentencepiece.bpe.model, as NowCaption 1.2.0's tools/export-translation-model.sh made it)
"""
import collections, json, sys
import ctranslate2, langid, sacrebleu
import sentencepiece as spm

asr, mt = sys.argv[1:3]
CODE = {'es_419': 'es', 'fr_fr': 'fr', 'de_de': 'de', 'pt_br': 'pt', 'it_it': 'it', 'ru_ru': 'ru',
        'cmn_hans_cn': 'zh', 'ja_jp': 'ja', 'ko_kr': 'ko', 'hi_in': 'hi', 'ar_eg': 'ar', 'tr_tr': 'tr'}
NEMO = ['es', 'it', 'pt', 'hi', 'ko', 'en', 'de', 'fr', 'ru', 'tr', 'vi', 'nl', 'ja', 'ar', 'uk', 'pl', 'no', 'fi', 'zh',
        'cs', 'bg', 'sk', 'sv', 'hr', 'ro', 'et', 'da', 'hu', 'el']
langid.set_languages(NEMO)
sp = spm.SentencePieceProcessor(model_file=f'{mt}/sentencepiece.bpe.model')
tr = ctranslate2.Translator(mt, device='cpu', intra_threads=4, compute_type='int8')
data = json.load(open(asr))


def run(lang_of):
    scores = []
    for lang, e in data.items():
        srcs = [it['asr'] or '.' for it in e['items']]
        batch = [[f'__{lang_of(lang, s)}__'] + sp.encode(s, out_type=str) + ['</s>'] for s in srcs]
        out = tr.translate_batch(batch, target_prefix=[['__en__']] * len(batch), beam_size=1, max_batch_size=8)
        hyps = [sp.decode(o.hypotheses[0][1:]) for o in out]
        scores.append(sacrebleu.corpus_chrf(hyps, [[it['english'] for it in e['items']]]).score)
    return sum(scores) / len(scores), scores


hits = collections.Counter()


def detect(lang, s):
    guess = langid.classify(s)[0]
    hits[guess == CODE[lang]] += 1
    return guess


for name, fn in (('true language', lambda l, s: CODE[l]), ('langid.py guess', detect),
                 ('always Spanish (wrong)', lambda l, s: 'es')):
    mean, per = run(fn)
    print(f'{name:24s} mean chrF {mean:5.1f}  ' + ' '.join(f'{l.split("_")[0]}:{v:.0f}' for l, v in zip(data, per)))
print('langid.py picked the right language for', hits[True], 'of', hits[True] + hits[False], 'sentences')
