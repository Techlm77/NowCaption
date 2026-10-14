"""Speech-to-English translation quality and speed on FLEURS clips.

Usage: eval_translate.py <fleurs-dir> <models-dir> <model> <threads> <out.json> <lang>...
model: whisper-small, whisper-medium (spoken language detected automatically), canary
(source language given; es, de and fr only). Scores are corpus BLEU and chrF per
language against the FLEURS English sentence, with sacrebleu's defaults.
"""
import json, os, sys, time
import sacrebleu
import soundfile as sf
import sherpa_onnx as so

fleurs, models, model, threads, out, langs = (sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]),
                                               sys.argv[5], sys.argv[6:])
CANARY_LANG = {'es_419': 'es', 'de_de': 'de', 'fr_fr': 'fr'}


def recognizer(lang):
    if model.startswith('whisper-'):
        size = model.split('-')[1]
        d = os.path.join(models, f'sherpa-onnx-whisper-{size}')
        return so.OfflineRecognizer.from_whisper(
            encoder=f'{d}/{size}-encoder.int8.onnx', decoder=f'{d}/{size}-decoder.int8.onnx',
            tokens=f'{d}/{size}-tokens.txt', language='', task='translate', num_threads=threads)
    d = os.path.join(models, 'sherpa-onnx-nemo-canary-180m-flash-en-es-de-fr-int8')
    return so.OfflineRecognizer.from_nemo_canary(
        encoder=f'{d}/encoder.int8.onnx', decoder=f'{d}/decoder.int8.onnx', tokens=f'{d}/tokens.txt',
        src_lang=CANARY_LANG[lang], tgt_lang='en', num_threads=threads)


results = {}
rec = None
for lang in langs:
    if model == 'canary' and lang not in CANARY_LANG:
        continue
    if rec is None or model == 'canary':
        rec = recognizer(lang)
    refs = json.load(open(os.path.join(fleurs, lang, 'refs.json')))
    hyps, gold, items = [], [], []
    compute = audio = 0.0
    for r in refs:
        x, rate = sf.read(os.path.join(fleurs, lang, r['audio']), dtype='float32', always_2d=False)
        s = rec.create_stream()
        s.accept_waveform(rate, x)
        t = time.perf_counter()
        rec.decode_stream(s)
        compute += time.perf_counter() - t
        audio += len(x) / rate
        res = s.result
        text = res.text.strip()  # one read per clip: the 1.13.8 .text leak does not matter here
        hyps.append(text)
        gold.append(r['english'])
        items.append({'audio': r['audio'], 'english': r['english'], 'hyp': text,
                      'lang': getattr(res, 'lang', '')})
    results[lang] = {'clips': len(refs), 'bleu': round(sacrebleu.corpus_bleu(hyps, [gold]).score, 1),
                     'chrf': round(sacrebleu.corpus_chrf(hyps, [gold]).score, 1),
                     'seconds_per_clip': round(compute / len(refs), 3), 'rtf': round(compute / audio, 3),
                     'items': items}
    print(model, lang, {k: v for k, v in results[lang].items() if k != 'items'}, flush=True)
json.dump(results, open(out, 'w'), ensure_ascii=False, indent=1)
