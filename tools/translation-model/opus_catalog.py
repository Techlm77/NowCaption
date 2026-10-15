"""Catalog OPUS X-en moses corpora for NowCaption's 33 languages -> data/catalog.json"""
import json, sys, urllib.request
LANGS = 'ar bg cs da de el es et fi fr he hi hr hu it ja ko lt lv nl no pl pt ro ru sk sl sv th tr uk vi zh'.split()
OPUS_CODE = {'no': 'nb', 'zh': 'zh'}  # OPUS uses nb for Norwegian Bokmål in most corpora
cat = {}
for l in LANGS:
    entries = []
    for code in {l, OPUS_CODE.get(l, l), 'no' if l == 'no' else l, 'zh_cn' if l == 'zh' else l}:
        a, b = sorted([code, 'en'])
        url = f'https://opus.nlpl.eu/opusapi/?source={a}&target={b}&preprocessing=moses&version=latest'
        req = urllib.request.Request(url, headers={'User-Agent': 'curl/8'})
        try:
            data = json.load(urllib.request.urlopen(req, timeout=60))['corpora']
        except Exception as e:
            print('ERR', l, code, e, file=sys.stderr); continue
        for c in data:
            entries.append({k: c[k] for k in ('corpus', 'version', 'alignment_pairs', 'size', 'url', 'source', 'target')})
    cat[l] = sorted(entries, key=lambda c: -int(c['alignment_pairs'] or 0))
    print(l, len(cat[l]), ' '.join(f"{c['corpus']}:{int(c['alignment_pairs'] or 0)//1000}k" for c in cat[l][:14]), flush=True)
json.dump(cat, open('data/catalog.json', 'w'), indent=1)
