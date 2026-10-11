#!/usr/bin/env python3
"""Capture-to-worker-event caption latency and CPU, through the app's audio path:
paplay -> PipeWire/PulseAudio null sink -> GStreamer capture -> recognition worker ->
punctuation -> caption event -> optional complete-text trace. GTK event delivery
and drawing are not measured. Scoring uses raw recognition words; copying and
recording complete traces add benchmark instrumentation overhead.

Clips are played in real time, one after another with a short gap, into a private null
sink, so nothing is heard. A word's lag is the time it first appears correctly in a
caption, minus the time it ends in the audio (from word_times.json). CPU is this
process's CPU time per second, measured separately over playback subprocess
intervals, settling gaps and the digital silence that follows. Missed timed
reference words and matched-word coverage accompany the latency percentiles.

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
    """Reference-to-hypothesis indexes for exact matches in a Levenshtein alignment."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    i, j, out = n, m, {}
    while i and j:
        if ref[i - 1] == hyp[j - 1] and d[i][j] == d[i - 1][j - 1]:
            out[i - 1] = j - 1; i -= 1; j -= 1
        elif d[i][j] == d[i - 1][j - 1] + 1:
            i -= 1; j -= 1
        elif d[i][j] == d[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    return out


def new_words(baseline, hyp):
    """Strip previous clips from a complete, cumulative caption trace.

    Window excerpts cannot identify repeated occurrences once text scrolls out
    of view. The worker's optional trace retains the full session instead.
    """
    if hyp[:len(baseline)] != baseline:
        raise ValueError('Caption trace lost its session prefix; use complete worker traces')
    return hyp[len(baseline):]


def score(plays, captions, times):
    """Per-word lag from complete worker traces, retaining words after they scroll away.
    Returns lags and per-word details. Events at the next clip's start are excluded."""
    lags, detail = [], []
    for k, (r, t0) in enumerate(plays):
        t_end = plays[k + 1][1] if k + 1 < len(plays) else float('inf')
        ref, ends = words(r['text']), times.get(r['audio'], {})
        before = [w for t, w in captions if t < t0]
        baseline = before[-1] if before else []
        history = []
        for t, hyp in captions:
            if t < t0 or t >= t_end:
                continue
            history.append((t, new_words(baseline, hyp)))
        if not history:
            continue
        # Align the final text once, so one occurrence cannot stand in for several
        # repeated reference words as the partial caption grows.
        final = history[-1][1]
        for i, hi in matches(ref, final).items():
            if str(i) in ends:
                t = next(t for t, hyp in history if len(hyp) > hi and hyp[hi] == final[hi])
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
    ap.add_argument('--gap', type=float, default=3, help='silence between clips (at least 3 seconds)')
    ap.add_argument('--dump', help='write every caption and per-word lag to this JSON file')
    a = ap.parse_args()
    if a.gap < 3 or a.silence <= 0 or a.stride < 1 or a.limit < 1:
        ap.error('gap must be >= 3, and silence, stride and limit must be positive')
    D = Path(a.data)
    refs = json.loads((D / 'refs.json').read_text())[::a.stride][:a.limit]
    if not refs:
        ap.error('No clips selected from refs.json')
    times = json.loads((D / 'word_times.json').read_text())

    sink = f'nowcaption_e2e_{os.getpid()}'
    mod = subprocess.check_output(['pactl', 'load-module', 'module-null-sink', f'sink_name={sink}']).decode().strip()
    events = queue.Queue()
    worker = engine.RecognitionWorker(
        lambda kind, value: events.put((time.monotonic(), kind, value)),
        on_trace=lambda text: events.put((time.monotonic(), 'caption-history', text)))
    capture = None

    def wait_for(expected, seconds):
        end = time.monotonic() + seconds
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'No {expected} event')
            _, kind, value = events.get(timeout=remaining)
            if kind in ('load-error', 'error', 'capture-error', 'overload'):
                raise RuntimeError(f'{kind}: {value}')
            if kind == expected:
                return value

    def drain(captions):
        while True:
            try:
                t, kind, value = events.get_nowait()
            except queue.Empty:
                return
            if kind in ('load-error', 'error', 'capture-error', 'overload'):
                raise RuntimeError(f'{kind}: {value}')
            if kind == 'caption-history':
                captions.append((t, words(value)))
            elif kind == 'warning':
                print(value, file=sys.stderr)

    worker.start()
    try:
        wait_for('ready', 60)
        capture = audio.Capture(worker.feed,
            on_started=lambda gen: events.put((time.monotonic(), 'capture-ready', gen)),
            on_ended=lambda gen, reason: events.put((time.monotonic(), 'capture-error', reason)))
        worker.set_source(1)
        capture.start(f'{sink}.monitor', 1)
        wait_for('capture-ready', 20)
        plays, captions = [], []
        drain(captions)
        playback_cpu = playback_wall = gap_cpu = gap_wall = 0.0
        for r in refs:
            cpu0, t0 = time.process_time(), time.monotonic()
            subprocess.run(['paplay', f'--device={sink}', str(D / r['audio'])], check=True, timeout=60)
            playback_cpu += time.process_time() - cpu0
            playback_wall += time.monotonic() - t0
            plays.append((r, t0))
            cpu0, wall0 = time.process_time(), time.monotonic()
            time.sleep(a.gap)
            gap_cpu += time.process_time() - cpu0
            gap_wall += time.monotonic() - wall0
            drain(captions)
        cpu1, wall1 = time.process_time(), time.monotonic()
        time.sleep(a.silence)
        silence_cpu = (time.process_time() - cpu1) / (time.monotonic() - wall1)

        drain(captions)
        lags, detail = score(plays, captions, times)
        timed_refs = sum(sum(str(i) in times.get(r['audio'], {}) for i in range(len(words(r['text']))))
                         for r, _ in plays)
        out = {'score_version': 3, 'metric_scope': 'capture_to_worker_event',
               'caption_trace': 'complete_session_raw_text',
               'clock_origin': 'paplay_process_launch', 'data': D.name, 'clips': len(plays),
               'reference_words': sum(len(words(r['text'])) for r, _ in plays),
               'timed_reference_words': timed_refs, 'timed_words': len(lags),
               'missed_timed_words': timed_refs - len(lags),
               'timed_word_coverage': round(len(lags) / timed_refs, 4) if timed_refs else None,
               'lag_s_p50': pct(lags, 50), 'lag_s_p90': pct(lags, 90), 'lag_s_p99': pct(lags, 99),
               'cpu_cores_playback': round(playback_cpu / playback_wall, 3),
               'cpu_cores_settling_gaps': round(gap_cpu / gap_wall, 4),
               'cpu_cores_silence': round(silence_cpu, 4),
               'peak_rss_mb': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}
        if a.dump:
            base = plays[0][1] if plays else 0
            Path(a.dump).write_text(json.dumps({
                'plays': [{'clip': r['audio'], 't0': round(t0 - base, 3), 'text': r['text']} for r, t0 in plays],
                'captions': [{'t': round(t - base, 3), 'text': ' '.join(w)} for t, w in captions],
                'words': detail}, indent=1))
        (ROOT / 'results').mkdir(exist_ok=True)
        (ROOT / f'results/e2e-v3-{D.name}.json').write_text(json.dumps(out, indent=1))
        print(json.dumps(out))
    finally:
        if capture:
            capture.stop()
            capture.wait_stopped(5)
        worker.stop()
        worker.join(2)
        subprocess.run(['pactl', 'unload-module', mod])


if __name__ == '__main__':
    main()
