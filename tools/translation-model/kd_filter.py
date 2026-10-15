"""Filter teacher translations (data/kd/<lang>.tsv) with the same checks as the training
data (build_data.keep: length ratio, script, English language ID, FLEURS sentences), plus
repetition loops. Rewrites each file in place and prints how many pairs were kept.
Usage: kd_filter.py <lang>...
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import fasttext
import build_data


def loops(e):
    w = e.lower().split()
    grams = [' '.join(w[i:i + 3]) for i in range(len(w) - 2)]
    return any(grams.count(g) >= 3 for g in set(grams))


build_data.LID = fasttext.load_model('data/lid.176.ftz')
banned = build_data.fleurs_english()
for lang in sys.argv[1:]:
    path = f'data/kd/{lang}.tsv'
    if not os.path.exists(path):
        continue
    kept, total = [], 0
    with open(path, newline='') as f:
        for line in f.read().split('\n'):
            p = line.split('\t')
            if len(p) != 3:
                continue
            total += 1
            r = build_data.keep(lang, p[0], p[1], banned)
            if r and not loops(r[1]):
                kept.append(f'{r[0]}\t{r[1]}\tKD\n')
    open(path + '.tmp', 'w').write(''.join(kept))
    os.replace(path + '.tmp', path)
    print(lang, 'kept', len(kept), 'of', total, flush=True)
