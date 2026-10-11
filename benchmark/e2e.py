#!/usr/bin/env python3
"""End-to-end caption latency and CPU, through the same path the app uses:
paplay -> PipeWire/PulseAudio null sink -> GStreamer capture -> recognition worker ->
punctuation -> caption event. Only GTK drawing is left out (it adds at most one frame).

Clips are played in real time, one after another with a short gap, into a private null
sink, so nothing is heard. A word's lag is the time it first appears correctly in a
caption, minus the time it ends in the audio (from word_times.json). CPU is this
process's CPU time (capture, recognition and punctuation threads) per second, measured
separately while clips play and during the digital silence that follows.

    PYTHONPATH=../src python e2e.py --data data/test-clean --limit 40
"""
import argparse, json, os, queue, re, resource, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'src'))
from nowcaption import audio, engine  # noqa: E402

ABBR = {'mr': 'mister', 'mrs': 'missus', 'dr': 'doctor', 'st': 'saint'}


def words(s):
    return [ABBR.get(w, w) for w in re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", s.lower())]


def matches(ref, hyp):
    """Indexes of ref words matched exactly in an alignment with hyp (Levenshtein backtrace)."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    i, j, out = n, m, set()
    while i and j:
        if ref[i - 1] == hyp[j - 1] and d[i][j] == d[i - 1][j - 1]:
            out.add(i - 1); i -= 1; j -= 1
        elif d[i][j] == d[i - 1][j - 1] + 1:
            i -= 1; j -= 1
        elif d[i][j] == d[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    return out


def new_words(baseline, hyp):
    """The words of caption hyp added after baseline (the caption when the clip started).
    A caption shows a moving window: some tail of the baseline, then the new words. Scoring
    only the new words keeps the alignment from pairing this clip's words with earlier text."""
    for k in range(len(baseline) + 1):
        tail = baseline[k:]
        if hyp[:len(tail)] == tail:
            return hyp[len(tail):]
    return hyp


def score(plays, captions, times):
    """Per-word lag: first caption time at which the word is shown correctly, minus its end in
    the audio. Returns lags and per-word details."""
    lags, detail = [], []
    for k, (r, t0) in enumerate(plays):
        t_end = plays[k + 1][1] if k + 1 < len(plays) else float('inf')
        ref, ends = words(r['text']), times.get(r['audio'], {})
        before = [w for t, w in captions if t < t0]
        baseline = before[-1] if before else []
        first = {}
        for t, hyp in captions:
            if t < t0 or t > t_end + 3:
                continue
            for i in matches(ref, new_words(baseline, hyp)):
                first.setdefault(i, t)
        for i, t in first.items():
            if str(i) in ends:
                lags.append(t - (t0 + ends[str(i)]))
                detail.append({'clip': r['audio'], 'word': ref[i], 'index': i,
                               'lag': round(lags[-1], 3), 'audio_end': ends[str(i)]})
    return lags, detail


def pct(v, q):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(q / 100 * len(v)))], 3) if v else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default=str(ROOT / 'data' / 'test-clean'))
    ap.add_argument('--limit', type=int, default=40)
    ap.add_argument('--stride', type=int, default=4, help='use every Nth clip (4 = one per speaker)')
    ap.add_argument('--silence', type=float, default=20, help='seconds of silence measured at the end')
    ap.add_argument('--dump', help='write every caption and per-word lag to this JSON file')
    a = ap.parse_args()
    D = Path(a.data)
    refs = json.loads((D / 'refs.json').read_text())[::a.stride][:a.limit]
    times = json.loads((D / 'word_times.json').read_text())

    sink = f'nowcaption_e2e_{os.getpid()}'
    mod = subprocess.check_output(['pactl', 'load-module', 'module-null-sink', f'sink_name={sink}']).decode().strip()
    events = queue.Queue()
    worker = engine.RecognitionWorker(lambda kind, value: events.put((time.monotonic(), kind, value)))
    worker.start()
    capture = audio.Capture(worker.feed)
    try:
        while events.get(timeout=60)[1] != 'ready':
            pass
        worker.set_source(1)
        capture.start(f'{sink}.monitor', 1)
        time.sleep(2)
        while not events.empty():
            events.get()
        plays = []
        cpu0, wall0 = time.process_time(), time.monotonic()
        for r in refs:
            t0 = time.monotonic()
            subprocess.run(['paplay', f'--device={sink}', str(D / r['audio'])], check=True)
            plays.append((r, t0))
            time.sleep(0.6)
        time.sleep(2)
        speech_cpu = (time.process_time() - cpu0) / (time.monotonic() - wall0)
        cpu1, wall1 = time.process_time(), time.monotonic()
        time.sleep(a.silence)
        silence_cpu = (time.process_time() - cpu1) / (time.monotonic() - wall1)

        captions = []
        while not events.empty():
            t, kind, value = events.get()
            if kind == 'caption':
                captions.append((t, words(value[1])))
            elif kind == 'overload':
                print('overload: skipped', value, 's', file=sys.stderr)
        lags, detail = score(plays, captions, times)
        out = {'data': D.name, 'clips': len(plays), 'timed_words': len(lags),
               'lag_s_p50': pct(lags, 50), 'lag_s_p90': pct(lags, 90), 'lag_s_p99': pct(lags, 99),
               'cpu_cores_speech': round(speech_cpu, 3), 'cpu_cores_silence': round(silence_cpu, 4),
               'peak_rss_mb': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}
        if a.dump:
            base = plays[0][1] if plays else 0
            Path(a.dump).write_text(json.dumps({
                'plays': [{'clip': r['audio'], 't0': round(t0 - base, 3), 'text': r['text']} for r, t0 in plays],
                'captions': [{'t': round(t - base, 3), 'text': ' '.join(w)} for t, w in captions],
                'words': detail}, indent=1))
        (ROOT / 'results').mkdir(exist_ok=True)
        (ROOT / f'results/e2e-{D.name}.json').write_text(json.dumps(out, indent=1))
        print(json.dumps(out))
    finally:
        capture.stop()
        worker.stop()
        subprocess.run(['pactl', 'unload-module', mod])


if __name__ == '__main__':
    main()
