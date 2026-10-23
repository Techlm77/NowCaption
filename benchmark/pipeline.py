#!/usr/bin/env python3
"""Continuous use: the app's own caption pipeline (nowcaption.engine.Captioner, with
punctuation, line splitting and silence handling) on one stream, as the app runs it.

The clips are joined by digital silence of random length (--gap, seeded) and fed in 40 ms
blocks. Each caption update is stamped with the audio clock plus the compute time so far,
so lag is measured as in bench.py: when a word first appeared correctly, minus when it ends
in the audio (word_times.json). WER is over the whole session's recognised words; scoring
uses the raw words, as punctuation never changes them. CPU is this thread's CPU time per
second of audio; --stages splits it by native call.
Needs PYTHONPATH=../src. Results go to results/pipeline-<data>-s<seed>.json.
"""
import argparse, json, random, tempfile, time
from pathlib import Path
import numpy as np, soundfile as sf

from bench import ROOT, align, words
from nowcaption import engine

BLOCK = 640   # 40 ms, as NowCaption captures audio
TAIL = 2.5    # seconds of silence after the last clip


class Timed:
    """Wraps a native object so that the time spent in each named method is added up."""
    def __init__(self, obj, spent, names):
        self._obj, self._spent, self._names = obj, spent, names
    def __getattr__(self, name):
        fn = getattr(self._obj, name)
        if name not in self._names:
            return fn
        def timed(*args):
            args = [a._obj if isinstance(a, Timed) else a for a in args]
            t = time.thread_time()  # ONNX Runtime with one thread runs on the calling thread
            try:
                out = fn(*args)
            finally:
                self._spent[self._names[name]] = self._spent.get(self._names[name], 0) + time.thread_time() - t
            return Timed(out, self._spent, STREAM) if name == 'create_stream' else out
        return timed

STREAM = {'accept_waveform': 'audio features (accept_waveform)'}
RECOGNIZER = {'create_stream': 'new stream', 'is_ready': 'is_ready', 'reset': 'stream reset',
              'decode_stream': 'speech model (decode_stream: encoder and greedy search)',
              'get_result': 'reading the recognised text (get_result)'}
PUNCT = {'add_punctuation_with_case': 'punctuation and casing'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', required=True, help='folder with refs.json, word_times.json and WAVs')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--gap', type=float, nargs=2, default=(2.0, 2.1), help='seconds of silence between clips')
    ap.add_argument('--stages', action='store_true', help='time each native call')
    a = ap.parse_args()
    D, rng = Path(a.data), random.Random(a.seed)
    refs = json.loads((D / 'refs.json').read_text())
    timing = json.loads((D / 'word_times.json').read_text())
    rec, punct = engine.load_models()
    spent = {}
    if a.stages:
        rec, punct = Timed(rec, spent, RECOGNIZER), Timed(punct, spent, PUNCT) if punct else None

    parts, starts, n = [np.zeros(rng.randrange(0, BLOCK), np.int16)], [], 0
    n = len(parts[0])
    for row in refs:
        x, sr = sf.read(D / row['audio'], dtype='int16'); assert sr == 16000
        starts.append(n / 16000); parts.append(x); n += len(x)
        g = np.zeros(int(rng.uniform(*a.gap) * 16000), np.int16); parts.append(g); n += len(g)
    parts.append(np.zeros(int(TAIL * 16000), np.int16))
    audio = np.concatenate(parts)

    events, clock = [], {}  # (time shown, finished lines so far, words still being spoken)
    def on_update(_text):
        events.append((clock['block_end'] + time.perf_counter() - clock['t0'], len(cap.raw), cap.partial))
    cap = engine.Captioner(rec, punct, on_update=on_update, keep_raw=True,
                           store=engine.TranscriptStore(tempfile.mkdtemp()))
    cpu0 = time.thread_time()
    for off in range(0, len(audio), BLOCK):
        clock['block_end'], clock['t0'] = (off + BLOCK) / 16000, time.perf_counter()
        cap.feed(audio[off:off + BLOCK].tobytes())
    cpu = time.thread_time() - cpu0
    audio_s = len(audio) / 16000

    line_words = [words(line) for line in cap.raw]
    before = np.cumsum([0] + [len(w) for w in line_words])
    flat = [w for ws in line_words for w in ws]
    shown = lambda e: flat[:before[e[1]]] + words(e[2])
    lags, items = [], []
    for k, row in enumerate(refs):
        s0, s1 = starts[k], starts[k + 1] if k + 1 < len(starts) else float('inf')
        earlier = [e for e in events if e[0] < s0]
        base = len(shown(earlier[-1])) if earlier else 0
        hist = [(e[0], shown(e)[base:]) for e in events if s0 <= e[0] < s1]
        ref, final = words(row['text']), hist[-1][1] if hist else []
        err, match = align(ref, final)
        ends = timing.get(row['audio'], {})
        for ri, hi in match.items():
            if str(ri) in ends:
                lags.append(next(t for t, ws in hist if len(ws) > hi and ws[hi] == final[hi]) - (s0 + ends[str(ri)]))
        items.append({'audio': row['audio'], 'hyp': ' '.join(final), 'errors': err})
    allref = [w for row in refs for w in words(row['text'])]
    errors, _ = align(allref, flat + words(cap.partial))

    pct = lambda v, q: round(float(np.percentile(v, q)), 3)
    out = {'data': D.name, 'seed': a.seed, 'gap_s': list(a.gap), 'words': len(allref), 'errors': errors,
           'wer': round(errors / len(allref), 4), 'lag_s_mean': round(float(np.mean(lags)), 3),
           'lag_s_p50': pct(lags, 50), 'lag_s_p90': pct(lags, 90), 'timed_words': len(lags),
           'audio_s': round(audio_s, 1), 'cpu_rtf': round(cpu / audio_s, 4)}
    if a.stages:
        out['stage_ms_per_audio_s'] = {k: round(v / audio_s * 1000, 2) for k, v in sorted(spent.items(), key=lambda kv: -kv[1])}
        out['stage_ms_per_audio_s']['everything else (Python)'] = round((cpu - sum(spent.values())) / audio_s * 1000, 2)
    print(json.dumps(out))
    out['items'] = items
    (ROOT / 'results').mkdir(exist_ok=True)
    (ROOT / f'results/pipeline-{D.name}-s{a.seed}.json').write_text(json.dumps(out, indent=1))

if __name__ == '__main__':
    main()
