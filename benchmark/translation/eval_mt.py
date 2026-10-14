"""Stage 2 of the cascade: text translation to English, scored with BLEU and chrF.

Translates (a) the true FLEURS transcripts and (b) Nemotron's ASR transcripts, so the
score of the cascade and of the translator alone can be compared.
Usage: eval_mt.py <nemotron-asr.json> <model> <out.json>
model: opus-mul (Helsinki-NLP/opus-mt-mul-en) or m2m-418m (facebook/m2m100_418M)
"""
import json, sys, time
import sacrebleu
import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

asr, model_name, out = sys.argv[1:4]
torch.set_num_threads(4)
HF = {'opus-mul': 'Helsinki-NLP/opus-mt-mul-en', 'm2m-418m': 'facebook/m2m100_418M'}[model_name]
CODE = {'es_419': 'es', 'fr_fr': 'fr', 'de_de': 'de', 'pt_br': 'pt', 'it_it': 'it', 'ru_ru': 'ru',
        'cmn_hans_cn': 'zh', 'ja_jp': 'ja', 'ko_kr': 'ko', 'hi_in': 'hi', 'ar_eg': 'ar', 'tr_tr': 'tr'}
tok = AutoTokenizer.from_pretrained(HF)
model = AutoModelForSeq2SeqLM.from_pretrained(HF).eval()


def translate(texts, lang):
    kwargs = {}
    if model_name.startswith('m2m'):
        tok.src_lang = CODE[lang]
        kwargs['forced_bos_token_id'] = tok.get_lang_id('en')
    outs = []
    for i in range(0, len(texts), 8):
        batch = tok(texts[i:i + 8], return_tensors='pt', padding=True, truncation=True, max_length=256)
        with torch.no_grad():
            gen = model.generate(**batch, num_beams=1, max_new_tokens=200, **kwargs)
        outs += tok.batch_decode(gen, skip_special_tokens=True)
    return outs


data = json.load(open(asr))
results = {}
for lang, entry in data.items():
    items = entry['items']
    gold = [i['english'] for i in items]
    t = time.perf_counter()
    from_truth = translate([i['source'] for i in items], lang)
    seconds = (time.perf_counter() - t) / len(items)
    from_asr = translate([i['asr'] or '.' for i in items], lang)
    score = lambda hyp: (round(sacrebleu.corpus_bleu(hyp, [gold]).score, 1), round(sacrebleu.corpus_chrf(hyp, [gold]).score, 1))
    results[lang] = {'truth_bleu_chrf': score(from_truth), 'cascade_bleu_chrf': score(from_asr),
                     'seconds_per_sentence': round(seconds, 3),
                     'examples': [{'english': g, 'cascade': c} for g, c in zip(gold[:3], from_asr[:3])]}
    print(model_name, lang, 'truth', results[lang]['truth_bleu_chrf'], 'cascade', results[lang]['cascade_bleu_chrf'],
          f'{seconds:.2f}s', flush=True)
json.dump(results, open(out, 'w'), ensure_ascii=False, indent=1)
