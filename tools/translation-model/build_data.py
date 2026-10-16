"""Build the filtered X->English training set for the 33 languages NowCaption translates.

Per language: a uniform sample of OpenSubtitles (spoken), the best-scored start of CCMatrix
(web text; the file is sorted by mining score), and the smaller clean corpora in full.
Pairs are filtered by length, script, fastText language ID on both sides, copy and
duplicate checks, and anything whose English matches a FLEURS sentence (the benchmark) is
dropped. Output: data/clean/<lang>.tsv  (source \t english \t corpus)
Usage: build_data.py <lang>...   (run with the venv python)
"""
import hashlib, json, os, random, re, sys, time, unicodedata
sys.path.insert(0, os.path.dirname(__file__))
import fasttext
import opus_stream
from data_manifest import LANGS, manifest_path, write_manifest

ROOT = os.getcwd()  # the work folder: data/ and runs/ live here
CAT = json.load(open(f'{ROOT}/data/catalog.json'))
LID = None

# corpus -> (mode, cap). 'sample' reads the whole corpus and keeps a uniform sample;
# 'prefix' reads from the start (CCMatrix is sorted best first) until cap pairs pass.
COMMON = [('OpenSubtitles', 'sample', 650_000), ('CCMatrix', 'prefix', 750_000), ('Europarl', 'sample', 120_000),
          ('WikiMatrix', 'sample', 120_000), ('Tatoeba', 'sample', 150_000), ('GlobalVoices', 'sample', 60_000),
          ('News-Commentary', 'sample', 100_000), ('SETIMES', 'sample', 80_000)]
EXTRA = {
    'zh': [('UNPC', 'sample', 150_000), ('ParaCrawl', 'prefix', 250_000)],
    'ja': [('JESC', 'sample', 400_000)],
    'ko': [('ParaCrawl', 'prefix', 250_000)],
    'th': [('SCB_MT_EN_TH', 'sample', 700_000)],
    'ar': [('UNPC', 'sample', 100_000)],
    'ru': [('UNPC', 'sample', 80_000)],
    'uk': [('ParaCrawl', 'prefix', 250_000)],
    'lv': [('ParaCrawl', 'prefix', 250_000)],
    'lt': [('ParaCrawl', 'prefix', 250_000)],
    'sk': [('ParaCrawl', 'prefix', 150_000)],
    'et': [('ParaCrawl', 'prefix', 150_000)],
}
OPENSUBS_URL = {'zh': 'https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/moses/en-zh_CN.txt.zip'}
FT_CODE = {'no': {'no', 'nb', 'nn'}, 'hr': {'hr', 'bs', 'sr'}, 'zh': {'zh'}}
SCRIPT = {  # languages whose source side must be mostly in this script
    'ar': r'[؀-ۿ]', 'he': r'[֐-׿]', 'el': r'[Ͱ-Ͽἀ-῿]',
    'bg': r'[Ѐ-ӿ]', 'ru': r'[Ѐ-ӿ]', 'uk': r'[Ѐ-ӿ]', 'hi': r'[ऀ-ॿ]',
    'th': r'[฀-๿]', 'ko': r'[가-힯ᄀ-ᇿ㄰-㆏]',
    'ja': r'[぀-ヿ㐀-鿿]', 'zh': r'[㐀-鿿]',
}
# characters of source per character of English, roughly
CHAR_RATIO = {'zh': 0.3, 'ja': 0.4, 'ko': 0.45, 'th': 0.9}
LETTER = re.compile(r'\w', re.UNICODE)
KANA = re.compile(r'[぀-ヿ]')


def norm(s):
    return re.sub(r'\W+', '', unicodedata.normalize('NFKC', s).lower())


def fleurs_english():
    path = f'{ROOT}/data/fleurs_en_norm.txt'
    if not os.path.exists(path):
        import csv, io, urllib.request
        csv.field_size_limit(10 ** 7)
        out = set()
        for split in ('train', 'dev', 'test'):
            url = f'https://huggingface.co/datasets/google/fleurs/resolve/main/data/en_us/{split}.tsv'
            rows = csv.reader(io.StringIO(urllib.request.urlopen(url).read().decode()), delimiter='\t', quoting=csv.QUOTE_NONE)
            out |= {norm(r[2]) for r in rows if len(r) > 2}
        open(path, 'w').write('\n'.join(sorted(out)))
    return set(open(path).read().split('\n'))


def url_for(lang, corpus):
    if corpus == 'OpenSubtitles' and lang in OPENSUBS_URL:
        return OPENSUBS_URL[lang], 22_000_000
    for e in CAT[lang]:
        if e['corpus'] == corpus:
            return e['url'], int(e['alignment_pairs'] or 0)
    return None, 0


def lid(text):
    labels, probs = LID.predict(text.replace('\n', ' '), k=1)
    return labels[0][9:], float(probs[0])


def keep(lang, x, e, banned):
    x, e = x.strip(), e.strip()
    if not x or not e or len(e) > 400 or len(x) > 400 or x == e:
        return None
    if len(LETTER.findall(e)) < 2:
        return None
    ratio = len(x) / CHAR_RATIO.get(lang, 1.0) / len(e)
    if not 0.4 <= ratio <= 2.5:
        return None
    if lang in SCRIPT:
        letters = LETTER.findall(x)
        if not letters or len(re.findall(SCRIPT[lang], x)) < 0.5 * len(letters):
            return None
        if lang == 'ja' and len(x) > 8 and not KANA.search(x):  # Han-only lines are usually Chinese
            return None
    # English side: mostly Latin letters, and fastText agrees
    letters = LETTER.findall(e)
    if sum(c.isascii() for c in letters) < 0.8 * len(letters):
        return None
    if len(e) > 25:
        code, p = lid(e)
        if code != 'en' or p < 0.5:
            return None
    if len(x) > 25 and lang not in SCRIPT:  # script check covers the others
        code, p = lid(x)
        if code not in FT_CODE.get(lang, {lang}) or p < 0.4:
            return None
    if norm(e) in banned:
        return None
    return x, e


def run(lang):
    global LID
    LID = fasttext.load_model(f'{ROOT}/data/lid.176.ftz')
    banned = fleurs_english()
    out_path = f'{ROOT}/data/clean/{lang}.tsv'
    if os.path.exists(out_path) and os.path.getsize(out_path):
        print(lang, 'exists, skipped', flush=True)
        return True
    seen, rows, rng, failed = set(), [], random.Random(sum(map(ord, lang))), []
    for corpus, mode, cap in COMMON + EXTRA.get(lang, []):
        url, total = url_for(lang, corpus)
        if not url:
            continue
        t, kept, read = time.time(), 0, 0
        p = 1.0 if mode == 'prefix' else min(1.0, 2.0 * cap / max(total, 1))
        bucket = []
        try:
            for x, e in opus_stream.pairs(url, lang):
                read += 1
                if mode == 'prefix' and read > 8 * cap:
                    break
                if p < 1 and rng.random() > p:
                    continue
                r = keep(lang, x, e, banned)
                if not r:
                    continue
                h = hashlib.blake2b(norm(r[0]).encode(), digest_size=8).digest()
                if h in seen:
                    continue
                seen.add(h)
                bucket.append(r)
                if mode == 'prefix' and len(bucket) >= cap:
                    break
        except Exception as ex:
            print(lang, corpus, 'FAILED', repr(ex)[:200], flush=True)
            failed.append(corpus)
        if len(bucket) > cap:
            bucket = rng.sample(bucket, cap)
        rows += [(x, e, corpus) for x, e in bucket]
        print(f'{lang} {corpus}: read {read} kept {len(bucket)} in {time.time() - t:.0f}s', flush=True)
    if failed or not rows:  # an existing file counts as finished, so a partial one is never written
        print(lang, 'NOT SAVED:', ', '.join(failed) if failed else 'no usable pairs',
              '; run again to retry', flush=True)
        return False
    rng.shuffle(rows)
    tmp = out_path + '.part'
    with open(tmp, 'w') as f:
        for x, e, c in rows:
            f.write(f'{x.replace(chr(9), " ")}\t{e.replace(chr(9), " ")}\t{c}\n')
    os.rename(tmp, out_path)
    print(lang, 'TOTAL', len(rows), flush=True)
    return True


def main(langs):
    langs = sorted(set(langs))
    if not langs or not set(langs) <= set(LANGS):
        raise ValueError('Pass one or more supported language codes to build_data.py')
    os.makedirs(f'{ROOT}/data/clean', exist_ok=True)
    path = manifest_path(f'{ROOT}/data')
    expected = set(langs)
    if path.exists():
        expected.update(json.loads(path.read_text())['languages'])
    # Retrying one language must not forget earlier requested languages or existing data.
    from pathlib import Path
    expected.update(p.stem for p in Path(f'{ROOT}/data/clean').glob('*.tsv'))
    write_manifest(f'{ROOT}/data', expected, complete=False)
    fleurs_english()
    from multiprocessing import Pool
    with Pool(int(os.environ.get('JOBS', 8))) as pool:
        results = pool.map(run, langs, chunksize=1)
    failed = [lang for lang, ok in zip(langs, results) if not ok]
    missing = [lang for lang in sorted(expected)
               if not os.path.isfile(f'{ROOT}/data/clean/{lang}.tsv')
               or not os.path.getsize(f'{ROOT}/data/clean/{lang}.tsv')]
    complete = not failed and not missing
    write_manifest(f'{ROOT}/data', expected, complete)
    if not complete:
        print('INCOMPLETE: retry', ' '.join(sorted(set(failed + missing))), file=sys.stderr, flush=True)
    return 0 if complete else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
