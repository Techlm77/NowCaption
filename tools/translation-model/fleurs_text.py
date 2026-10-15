"""FLEURS dev/test text for the 33 languages: unique sentence ids with the source
transcription and the en_us transcription of the same id as reference.
-> data/eval/fleurs_<split>.json {lang: [{id, source, english}]}"""
import csv, io, json, os, urllib.request
csv.field_size_limit(10 ** 7)
BASE = 'https://huggingface.co/datasets/google/fleurs/resolve/main/data'
FLEURS = {'ar': 'ar_eg', 'bg': 'bg_bg', 'cs': 'cs_cz', 'da': 'da_dk', 'de': 'de_de', 'el': 'el_gr', 'es': 'es_419',
          'et': 'et_ee', 'fi': 'fi_fi', 'fr': 'fr_fr', 'he': 'he_il', 'hi': 'hi_in', 'hr': 'hr_hr', 'hu': 'hu_hu',
          'it': 'it_it', 'ja': 'ja_jp', 'ko': 'ko_kr', 'lt': 'lt_lt', 'lv': 'lv_lv', 'nl': 'nl_nl', 'no': 'nb_no',
          'pl': 'pl_pl', 'pt': 'pt_br', 'ro': 'ro_ro', 'ru': 'ru_ru', 'sk': 'sk_sk', 'sl': 'sl_si', 'sv': 'sv_se',
          'th': 'th_th', 'tr': 'tr_tr', 'uk': 'uk_ua', 'vi': 'vi_vn', 'zh': 'cmn_hans_cn'}


def tsv(code, split):
    with urllib.request.urlopen(f'{BASE}/{code}/{split}.tsv') as r:
        return list(csv.reader(io.StringIO(r.read().decode()), delimiter='\t', quoting=csv.QUOTE_NONE))


if __name__ == '__main__':
    for split in ('dev', 'test'):
        english = {}
        for row in tsv('en_us', split):
            english.setdefault(row[0], row[2])
        out = {}
        for lang, code in FLEURS.items():
            items, seen = [], set()
            for row in tsv(code, split):
                if row[0] in english and row[0] not in seen:
                    seen.add(row[0])
                    items.append({'id': row[0], 'source': row[2], 'english': english[row[0]]})
            out[lang] = items
            print(split, lang, len(items), flush=True)
        os.makedirs('data/eval', exist_ok=True)
        json.dump(out, open(f'data/eval/fleurs_{split}.json', 'w'), ensure_ascii=False, indent=0)
