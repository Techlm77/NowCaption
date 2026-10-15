"""Speed, memory and reliability of a translation model, run as NowCaption runs it
(one sentence at a time, 2 threads, int8).
Usage: compare.py <kind m2m|nc> <model-dir> <out.json>
"""
import json, os, random, re, resource, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import eval_ct2

ROOT = os.getcwd()  # the work folder: data/ and runs/ live here


def rss_mb():
    with open('/proc/self/status') as f:
        for line in f:
            if line.startswith('VmRSS'):
                return int(line.split()[1]) // 1024


def runaway(hyp, src_len_words):
    """A repetition loop, or far more English than the source could hold."""
    words = hyp.split()
    grams = [' '.join(words[i:i + 3]).lower() for i in range(len(words) - 2)]
    loop = any(grams.count(g) >= 3 for g in set(grams))
    long = len(words) > 3 * max(src_len_words, 3) + 8
    return loop or long


def words(text):
    # CJK and Thai have no spaces: count characters / 2 as a rough word count
    n = len(text.split())
    return max(n, len(re.sub(r'\s', '', text)) // 2) if n <= 2 else n


def main():
    kind, d, out = sys.argv[1:4]
    base = rss_mb()
    t = time.perf_counter()
    m = eval_ct2.load(kind, d, 2)
    load_s = time.perf_counter() - t
    loaded = rss_mb()
    test = json.load(open(f'{ROOT}/data/eval/fleurs_test.json'))
    rng = random.Random(5)
    sample = [(l, it) for l in sorted(test) for it in rng.sample(test[l], 8)]  # 264 sentences
    lat, outs = [], []
    m.translate(['Hola, ¿cómo estás?'], batch=1)  # warm-up
    for lang, it in sample:
        t = time.perf_counter()
        hyp = m.translate([it['source']], batch=1)[0]
        lat.append((time.perf_counter() - t) * 1000)
        outs.append(hyp)
    lat_sorted = sorted(lat)
    peak = rss_mb()
    # half-finished sentences, as the live re-translation sees them
    partial_runaway, partial_n = 0, 0
    for lang in sorted(test):
        for it in test[lang][:40]:
            src = it['source']
            for frac in (0.3, 0.6):
                if len(src.split()) > 3:
                    cut = ' '.join(src.split()[:max(1, int(len(src.split()) * frac))])
                else:
                    cut = src[:max(1, int(len(src) * frac))]
                partial_n += 1
                partial_runaway += runaway(m.translate([cut], batch=1)[0], words(cut))
    full_runaway = 0
    full_n = 0
    for lang in sorted(test):
        hyps = m.translate([it['source'] for it in test[lang]], batch=16)
        for it, h in zip(test[lang], hyps):
            full_n += 1
            full_runaway += runaway(h, words(it['source']))
    junk = ['uh', 'mm-hmm', '[music]', '...', 'eh eh eh eh', 'ja ja ja', '2024', 'OK', 'ah, sí', 'ありがとう',
            'Ну', '음', 'la la la la la la la la', 'Hmm.', '¿Eh?', 'Merci.']
    junk_out = {j: m.translate([j], batch=1)[0] for j in junk}
    res = {'kind': kind, 'model': d,
           'model_mb': round(sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d)) / 1e6),
           'load_seconds': round(load_s, 2), 'rss_added_mb': loaded - base, 'rss_after_translating_mb': peak - base,
           'latency_ms': {'mean': round(sum(lat) / len(lat)), 'p50': round(lat_sorted[len(lat) // 2]),
                          'p95': round(lat_sorted[int(len(lat) * 0.95)]), 'max': round(lat_sorted[-1])},
           'runaway_full': [full_runaway, full_n], 'runaway_partial': [partial_runaway, partial_n],
           'junk': junk_out, 'sample_outputs': [(l, it['source'], h) for (l, it), h in list(zip(sample, outs))[::33]]}
    json.dump(res, open(out, 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ('junk', 'sample_outputs')}))
    for k, v in junk_out.items():
        print(f'  {k!r} -> {v!r}')


if __name__ == '__main__':
    main()
