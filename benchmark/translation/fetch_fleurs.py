"""Fetch a sample of FLEURS test recordings with their English reference translations.

FLEURS sentences come from FLoRes, so a sentence id has the same meaning in every
language; the en_us transcription of that id is the English reference.
Streams each language's test.tar.gz and stops after N recordings, so only part of
each archive is downloaded. Usage: fetch_fleurs.py <out-dir> <N> <lang>...
"""
import csv, io, json, os, sys, tarfile, urllib.request

BASE = 'https://huggingface.co/datasets/google/fleurs/resolve/main/data'
out, n, langs = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
csv.field_size_limit(10**7)


def tsv(lang):
    with urllib.request.urlopen(f'{BASE}/{lang}/test.tsv') as r:
        rows = list(csv.reader(io.StringIO(r.read().decode()), delimiter='\t', quoting=csv.QUOTE_NONE))
    return {row[1]: (row[0], row[2]) for row in rows}  # file name -> (sentence id, raw transcription)


english = {}
for fname, (sid, text) in tsv('en_us').items():
    english.setdefault(sid, text)
for lang in langs:
    rows = tsv(lang)
    folder = os.path.join(out, lang)
    os.makedirs(folder, exist_ok=True)
    items, seen = [], set()
    with urllib.request.urlopen(f'{BASE}/{lang}/audio/test.tar.gz') as r, tarfile.open(fileobj=r, mode='r|gz') as tar:
        for m in tar:
            name = os.path.basename(m.name)
            if not m.isfile() or not name.endswith('.wav') or name not in rows:
                continue
            sid, text = rows[name]
            if sid not in english or sid in seen:  # one recording per sentence
                continue
            data = tar.extractfile(m).read()
            with open(os.path.join(folder, name), 'wb') as f:
                f.write(data)
            seen.add(sid)
            items.append({'audio': name, 'id': sid, 'source': text, 'english': english[sid]})
            if len(items) >= n:
                break
    with open(os.path.join(folder, 'refs.json'), 'w') as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    print(lang, len(items), 'clips', flush=True)
