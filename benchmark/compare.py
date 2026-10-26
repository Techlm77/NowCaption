#!/usr/bin/env python3
"""Tables from stream.py's results on the ESB sets: models side by side, with paired ranges against one of them.

    python compare.py --models v2 v3 --base v2 [--json results/compare.json]

A model NAME means stream.py runs named NAME-cont (app:, continuous use) and NAME-fresh (appreset:, a fresh
stream per clip) on all eight sets of data/esb, i.e. results/stream/NAME-<cont|fresh>-<set>-s0.json.

Per model and mode (cont = continuous use, fresh = fresh stream per clip):
- WER per set after Whisper's English normaliser (as live.py), the 8-set average (plain mean of the sets, as
  the README's table) and Common Voice;
- latency of every timed word the model got right: "settled" (when it last changed: the final caption) and
  "first" (when it first appeared at its final place, the README's definition), median / p90 / p99;
- word loops: clips whose final text repeats one word 4 or more times in a row;
- CPU seconds per second of audio (mean over the sets) and peak memory (largest set).
Paired against --base on the same clips: for each set the clips are resampled RESAMPLES times (seed 0), the
same draw for both models; the WER change gets a 95% range, and so does the change in median and p90
settled latency over the words both models timed. A change is beyond luck when its range excludes 0.
The Pareto frontier: models no other model beats on both the 8-set average WER and the median settled
latency, per mode.
"""
import argparse
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
SETS = ['librispeech-clean', 'librispeech-other', 'common_voice', 'ami', 'voxpopuli', 'earnings22', 'gigaspeech', 'urgent2024']
MODES = ['cont', 'fresh']
RESAMPLES, SEED = 2000, 0
_norm = None


RESULTS = ROOT / 'results' / 'stream'
DATA = ROOT / 'data' / 'esb'  # where the sets' refs.jsonl are, for results published without reference texts


def load(model, mode, s):
    p = RESULTS / f'{model}-{mode}-{s}-s0.json'
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    if any('ref' not in it for it in d.get('items', [])):
        refs = {r['audio']: r['text'] for r in map(json.loads, open(DATA / s / 'refs.jsonl'))}
        for it in d['items']:
            it.setdefault('ref', refs[it['audio']])
    return d


def clip_errors(items):
    """audio -> (errors, words) after Whisper's normaliser, for clips whose reference isn't empty."""
    global _norm
    import jiwer
    if _norm is None:
        from whisper_normalizer.english import EnglishTextNormalizer
        _norm = EnglishTextNormalizer()
    pairs = [(i['audio'], _norm(i['ref']), _norm(i['hyp'])) for i in items]
    pairs = [p for p in pairs if p[1].strip()]
    out = jiwer.process_words([r for _, r, _ in pairs], [h for _, _, h in pairs])
    count = lambda c: c.hyp_end_idx - c.hyp_start_idx if c.type == 'insert' else c.ref_end_idx - c.ref_start_idx  # noqa: E731
    return {a: (sum(count(c) for c in al if c.type != 'equal'), len(r.split())) for (a, r, _), al in zip(pairs, out.alignments)}


def loops(hyp):
    w = hyp.split()
    return any(len(set(w[i:i + 4])) == 1 for i in range(len(w) - 3))


def lags(items, key):
    """(audio, ref word index) -> latency."""
    return {(i['audio'], k): v for i in items for k, v in i.get(key, {}).items()}


def summary(model, mode):
    rows = {s: load(model, mode, s) for s in SETS}
    if any(v is None for v in rows.values()):
        return None
    out = {'model': model, 'mode': mode, 'sets': {}}
    settled, first = [], []
    for s, d in rows.items():
        st = [v for i in d['items'] for v in i.get('settled', {}).values()]
        fi = [v for i in d['items'] for v in i['lags'].values()]
        settled += st
        first += fi
        out['sets'][s] = {'wer': d['wer'], 'settled_p50': float(np.median(st)) if st else None,
                          'loops': sum(loops(i['hyp']) for i in d['items']), 'cpu_rtf': d['cpu_rtf'],
                          'peak_rss_mb': d['peak_rss_mb'], 'resets': d.get('resets'), 'clips': d.get('clips_total')}
    pc = lambda v, q: round(float(np.percentile(v, q)), 3) if v else None  # noqa: E731  (sets without word_times.json have no delays)
    out.update(avg_wer=float(np.mean([out['sets'][s]['wer'] for s in SETS])), cv_wer=out['sets'].get('common_voice', {}).get('wer'),
               settled=[pc(settled, q) for q in (50, 90, 99)], first=[pc(first, q) for q in (50, 90, 99)],
               timed=len(settled), loops=sum(v['loops'] for v in out['sets'].values()),
               cpu_rtf=float(np.mean([v['cpu_rtf'] for v in out['sets'].values()])),
               peak_rss_mb=max(v['peak_rss_mb'] for v in out['sets'].values()))
    return out


def paired(new, old, mode):
    """Change new minus old: WER per set and the 8-set average, median and p90 settled latency, with ranges."""
    rng = np.random.default_rng(SEED)
    per_set, draws_avg = {}, []
    lat_n, lat_o, lat_clip = [], [], []
    cells = []
    for si, s in enumerate(SETS):
        a, b = load(new, mode, s), load(old, mode, s)
        en, eo = clip_errors(a['items']), clip_errors(b['items'])
        keys = sorted(en.keys() & eo.keys())
        n = np.array([en[k][0] for k in keys]); o = np.array([eo[k][0] for k in keys]); w = np.array([en[k][1] for k in keys])
        cells.append((n, o, w))
        ln, lo = lags(a['items'], 'settled'), lags(b['items'], 'settled')
        both = sorted(ln.keys() & lo.keys())
        idx = {k: j for j, k in enumerate(keys)}
        lat_n += [ln[k] for k in both]; lat_o += [lo[k] for k in both]
        lat_clip += [si * 100000 + idx.get(k[0], -1) for k in both]
    lat_n, lat_o, lat_clip = np.array(lat_n), np.array(lat_o), np.array(lat_clip)
    timed = len(lat_n) > 0
    # one draw of clips per set and resample, shared by WER and latency
    wer_draws = np.zeros((RESAMPLES, len(SETS)))
    sel = []
    for si, (n, o, w) in enumerate(cells):
        idx = rng.integers(0, len(w), size=(RESAMPLES, len(w)))
        wer_draws[:, si] = (n[idx].sum(1) - o[idx].sum(1)) / w[idx].sum(1)
        sel.append(idx)
        per_set[SETS[si]] = [float((n.sum() - o.sum()) / w.sum()), *np.percentile(wer_draws[:, si], [2.5, 97.5]).tolist()]
    avg = [float(np.mean([v[0] for v in per_set.values()])), *np.percentile(wer_draws.mean(1), [2.5, 97.5]).tolist()]
    # latency: words of the drawn clips (with repeats), as clip weights
    order = np.argsort(lat_clip, kind='stable')
    lat_clip, lat_n, lat_o = lat_clip[order], lat_n[order], lat_o[order]
    uniq, start = np.unique(lat_clip, return_index=True)
    end = np.append(start[1:], len(lat_clip))
    span = {int(c): (int(a), int(b)) for c, a, b in zip(uniq, start, end)}
    d50, d90 = [], []
    for r in range(min(RESAMPLES, 500) if timed else 0):  # 500 draws: enough for a latency range, and much faster
        take = []
        for si, idx in enumerate(sel):
            counts = np.bincount(idx[r], minlength=len(cells[si][2]))
            for j in np.nonzero(counts)[0]:
                sp = span.get(si * 100000 + int(j))
                if sp:
                    take += [np.arange(*sp)] * int(counts[j])
        t = np.concatenate(take)
        d50.append(np.median(lat_n[t]) - np.median(lat_o[t]))
        d90.append(np.percentile(lat_n[t], 90) - np.percentile(lat_o[t], 90))
    lat50 = [float(np.median(lat_n) - np.median(lat_o)), *np.percentile(d50, [2.5, 97.5]).tolist()] if timed else None
    lat90 = [float(np.percentile(lat_n, 90) - np.percentile(lat_o, 90)), *np.percentile(d90, [2.5, 97.5]).tolist()] if timed else None
    return {'sets': per_set, 'avg_wer': avg, 'settled_p50': lat50, 'settled_p90': lat90, 'paired_words': int(len(lat_n))}


def after_pause(model, mode, sets=None, head=2):
    """Speech after a pause: every clip starts after 2 s of silence. For the first `head` reference words of
    each clip against the rest: the share wrong (substituted or deleted, after Whisper's normaliser) and the
    median settled delay of the words timed. Returns {'first': (wrong, words, p50), 'rest': (...)}."""
    global _norm
    import jiwer
    if _norm is None:
        from whisper_normalizer.english import EnglishTextNormalizer
        _norm = EnglishTextNormalizer()
    acc = {'first': [0, 0, []], 'rest': [0, 0, []]}
    for s in sets or SETS:
        d = load(model, mode, s)
        for it in d['items']:
            ref, hyp = _norm(it['ref']).split(), _norm(it['hyp']).split()
            if not ref:
                continue
            ok = [False] * len(ref)
            for c in jiwer.process_words(' '.join(ref), ' '.join(hyp) or '<empty>').alignments[0]:
                if c.type == 'equal':
                    for i in range(c.ref_start_idx, c.ref_end_idx):
                        ok[i] = True
            for i, good in enumerate(ok):
                k = 'first' if i < head else 'rest'
                acc[k][0] += not good
                acc[k][1] += 1
            # settled delays are keyed by live.py's own word split; the clip's first words are its first keys
            for k, v in it.get('settled', {}).items():
                acc['first' if int(k) < head else 'rest'][2].append(v)
    return {k: (w, n, float(np.median(l)) if l else None) for k, (w, n, l) in acc.items()}


def verdict(r, limit=0.0):
    return 'better beyond luck' if r[2] < 0 else 'worse beyond luck' if r[1] > limit else 'within luck'


def pareto(rows):
    if any(r['settled'][0] is None for r in rows):
        return []  # no word timings, no delay axis
    out = []
    for a in rows:
        if not any(b is not a and b['avg_wer'] <= a['avg_wer'] and b['settled'][0] <= a['settled'][0]
                   and (b['avg_wer'] < a['avg_wer'] or b['settled'][0] < a['settled'][0]) for b in rows):
            out.append(a['model'])
    return out


def main():
    global RESULTS, SETS, DATA
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--json', default=str(ROOT / 'results' / 'compare.json'))
    ap.add_argument('--models', nargs='+', required=True)
    ap.add_argument('--results', default=str(RESULTS), help='folder of stream.py results (default results/stream)')
    ap.add_argument('--sets', nargs='+', help='set names (default: the eight ESB sets)')
    ap.add_argument('--data', default=str(DATA), help='folder of the sets (for results without reference texts)')
    a = ap.parse_args()
    RESULTS, DATA = Path(a.results), Path(a.data)
    if a.sets:
        SETS = a.sets
    models = a.models
    report = {'base': a.base, 'modes': {}}
    for mode in MODES:
        rows = [r for m in models if (r := summary(m, mode))]
        for r in rows:
            if r['model'] != a.base and any(x['model'] == a.base for x in rows):
                r['vs_base'] = paired(r['model'], a.base, mode)
        report['modes'][mode] = {'rows': rows, 'pareto': pareto(rows)}
        print(f'\n## {"Continuous use" if mode == "cont" else "Fresh stream per clip"} ({len(rows)} models complete)\n')
        print('| Model | 8-set avg WER | vs base (95% range) | Common Voice | Settled p50 / p90 / p99 (s) | vs base median (ms) | First p50 / p90 | Loops | CPU | Peak MB |')
        print('|---|---|---|---|---|---|---|---|---|---|')
        for r in sorted(rows, key=lambda r: r['avg_wer']):
            v = r.get('vs_base')
            w = f"{v['avg_wer'][0]*100:+.2f} ({v['avg_wer'][1]*100:+.2f} to {v['avg_wer'][2]*100:+.2f})" if v else '—'
            l = f"{v['settled_p50'][0]*1000:+.0f} ({v['settled_p50'][1]*1000:+.0f} to {v['settled_p50'][2]*1000:+.0f})" if v and v['settled_p50'] else '—'
            star = ' ★' if r['model'] in report['modes'][mode]['pareto'] else ''
            cv = '—' if r['cv_wer'] is None else f"{r['cv_wer'] * 100:.2f}%"
            lat = (f"{r['settled'][0]:.3f} / {r['settled'][1]:.3f} / {r['settled'][2]:.3f} | {l} | {r['first'][0]:.3f} / {r['first'][1]:.3f}"
                   if r['settled'][0] is not None else '— (no word timings) | — | —')
            print(f"| {r['model']}{star} | {r['avg_wer'] * 100:.2f}% | {w} | {cv} | {lat} | {r['loops']} | {r['cpu_rtf']:.3f} | {r['peak_rss_mb']} |")
        print('\n★ = on the Pareto frontier (no model has both a lower average WER and a lower median settled latency).')
        if rows:
            print('\nPer set, WER change against the base in points (95% range); worse beyond luck and over +0.5 breaks the ship rule:\n')
            print('| Model | ' + ' | '.join(SETS) + ' |')
            print('|---|' + '---|' * len(SETS))
            for r in rows:
                if 'vs_base' in r:
                    cells = []
                    for s in SETS:
                        c = r['vs_base']['sets'][s]
                        flag = ' ✗' if c[1] > 0 and c[0] > 0.005 else ''
                        cells.append(f"{c[0]*100:+.2f} ({c[1]*100:+.1f}…{c[2]*100:+.1f}){flag}")
                    print(f"| {r['model']} | " + ' | '.join(cells) + ' |')
        if rows:
            print('\nSpeech after a pause (each clip follows 2 s of silence): the first 2 words of every clip against the rest\n')
            print('| Model | First 2 words wrong | Other words wrong | First 2 words delay p50 | Others p50 | Common Voice: first 2 wrong / delay p50 |')
            print('|---|---|---|---|---|---|')
            for r in sorted(rows, key=lambda r: r['avg_wer']):
                ap = after_pause(r['model'], mode)
                cv = after_pause(r['model'], mode, ['common_voice']) if 'common_voice' in SETS else ap
                r['after_pause'], r['after_pause_cv'] = ap, cv
                f = lambda t: f"{t[0] / t[1] * 100:.1f}%"  # noqa: E731
                g = lambda x: '—' if x is None else f'{x:.3f} s'  # noqa: E731
                print(f"| {r['model']} | {f(ap['first'])} | {f(ap['rest'])} | {g(ap['first'][2])} | {g(ap['rest'][2])} | "
                      f"{f(cv['first'])} / {g(cv['first'][2])} |")
    Path(a.json).write_text(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
