"""Score a CTranslate2 translation model the way NowCaption runs it.

kind m2m: the shipped M2M100 418M int8 (langid.py picks the source language, as in 1.2.0)
kind nc:  the new many-to-English model (no language tag)
data: fleurs_dev / fleurs_test (true transcripts) or asr (Nemotron transcripts of FLEURS clips)
Usage: eval_ct2.py <kind> <model-dir> <data> <out.json> [threads]
"""
import json, os, sys, time
import ctranslate2, sacrebleu
import sentencepiece as spm

ROOT = os.getcwd()  # the work folder: data/ and runs/ live here
FLEURS = {'ar_eg': 'ar', 'bg_bg': 'bg', 'cs_cz': 'cs', 'da_dk': 'da', 'de_de': 'de', 'el_gr': 'el', 'es_419': 'es',
          'et_ee': 'et', 'fi_fi': 'fi', 'fr_fr': 'fr', 'he_il': 'he', 'hi_in': 'hi', 'hr_hr': 'hr', 'hu_hu': 'hu',
          'it_it': 'it', 'ja_jp': 'ja', 'ko_kr': 'ko', 'lt_lt': 'lt', 'lv_lv': 'lv', 'nb_no': 'no', 'nl_nl': 'nl',
          'pl_pl': 'pl', 'pt_br': 'pt', 'ro_ro': 'ro', 'ru_ru': 'ru', 'sk_sk': 'sk', 'sl_si': 'sl', 'sv_se': 'sv',
          'th_th': 'th', 'tr_tr': 'tr', 'uk_ua': 'uk', 'vi_vn': 'vi', 'cmn_hans_cn': 'zh'}
LANGS = sorted(set(FLEURS.values())) + ['en']


class M2M:
    def __init__(self, d, threads):
        from langid.langid import LanguageIdentifier, model
        self.tr = ctranslate2.Translator(d, device='cpu', intra_threads=threads, compute_type='int8')
        self.sp = spm.SentencePieceProcessor(model_file=f'{d}/sentencepiece.bpe.model')
        self.lid = LanguageIdentifier.from_modelstring(model)
        self.lid.set_languages(sorted(set(LANGS) | {'nb', 'nn'}))

    def lang(self, text):
        code = self.lid.classify(text)[0]
        return 'no' if code in ('nb', 'nn') else code

    def translate(self, texts, batch=16):
        langs = [self.lang(t) for t in texts]
        out = [None] * len(texts)
        todo = [i for i, l in enumerate(langs) if l != 'en']
        for i, l in enumerate(langs):
            if l == 'en':
                out[i] = texts[i]
        toks = [[f'__{langs[i]}__'] + self.sp.encode(texts[i], out_type=str) + ['</s>'] for i in todo]
        res = self.tr.translate_batch(toks, target_prefix=[['__en__']] * len(toks), beam_size=1,
                                      max_decoding_length=256, max_batch_size=batch)
        for i, r in zip(todo, res):
            out[i] = self.sp.decode(r.hypotheses[0][1:]).strip()
        return out


class NC:
    def __init__(self, d, threads):
        self.tr = ctranslate2.Translator(d, device='cpu', intra_threads=threads, compute_type='int8')
        self.s = spm.SentencePieceProcessor(model_file=f'{d}/source.spm')
        self.t = spm.SentencePieceProcessor(model_file=f'{d}/target.spm')

    def translate(self, texts, batch=16):
        toks = [self.s.encode(t, out_type=str)[:255] for t in texts]
        res = self.tr.translate_batch(toks, beam_size=1, max_decoding_length=256, max_batch_size=batch)
        return [self.t.decode_pieces(r.hypotheses[0]).strip() for r in res]


class M2MTrueLanguage(M2M):
    """M2M100 told the true language (as a distillation teacher would be)."""
    lang_hint = None

    def lang(self, text):
        return self.lang_hint


class Madlad:
    """MADLAD-400 MT (T5): '<2en> ' + text, no source language needed."""

    def __init__(self, d, threads):
        self.tr = ctranslate2.Translator(d, device='cpu', intra_threads=threads, compute_type='int8')
        self.sp = spm.SentencePieceProcessor(model_file=f'{d}/spiece.model')

    def translate(self, texts, batch=16):
        toks = [self.sp.encode('<2en> ' + t, out_type=str) + ['</s>'] for t in texts]
        res = self.tr.translate_batch(toks, beam_size=1, max_decoding_length=256, max_batch_size=batch)
        return [self.sp.decode(r.hypotheses[0]).strip() for r in res]


def load(kind, d, threads):
    return {'m2m': M2M, 'm2m-true': M2MTrueLanguage, 'madlad': Madlad, 'nc': NC}[kind](d, threads)


def dataset(name):
    if name == 'asr':
        raw = json.load(open(f'{ROOT}/data/eval/nemotron_asr.json'))
        return {FLEURS[k]: [{'source': it['asr'] or '.', 'english': it['english']} for it in v['items']] for k, v in raw.items()}
    return json.load(open(f'{ROOT}/data/eval/{name}.json'))


if __name__ == '__main__':
    kind, d, name, out = sys.argv[1:5]
    threads = int(sys.argv[5]) if len(sys.argv) > 5 else 8
    m = load(kind, d, threads)
    data = dataset(name)
    res, t0 = {}, time.time()
    limit = int(os.environ.get('EVAL_LIMIT', 0)) or None
    for lang in sorted(data):
        items = data[lang][:limit]
        if isinstance(m, M2MTrueLanguage):
            m.lang_hint = lang
        hyps = m.translate([it['source'] for it in items])
        refs = [it['english'] for it in items]
        res[lang] = {'chrf': round(sacrebleu.corpus_chrf(hyps, [refs]).score, 2),
                     'bleu': round(sacrebleu.corpus_bleu(hyps, [refs]).score, 2), 'hyps': hyps}
        print(lang, res[lang]['chrf'], flush=True)
    mean = sum(r['chrf'] for r in res.values()) / len(res)
    json.dump({'kind': kind, 'model': d, 'data': name, 'mean_chrf': round(mean, 2), 'langs': res,
               'seconds': round(time.time() - t0)}, open(out, 'w'), ensure_ascii=False, indent=0)
    print('MEAN chrF', round(mean, 2), 'in', round(time.time() - t0), 's')
