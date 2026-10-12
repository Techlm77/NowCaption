#!/usr/bin/env python3
"""Engine-only comparison: stream labelled 16 kHz WAVs straight into one recognizer.

For every block of audio fed (100 ms, or --chunk-ms), the recognizer's text is recorded with the audio clock
plus the compute time of that step. That gives, per reference word, when the engine
first produced it correctly. It leaves out audio capture, NowCaption's caption
logic, punctuation and drawing (see e2e.py for those), and assumes the engine keeps
up with real time (it does here: CPU is well under one core). CPU is per second of
audio including 1.5 s of silence after each clip.
Run one engine per process. Results go to results/<name>-<data>-<cond>-t<threads>.json.
"""
import argparse, ctypes as c, json, os, re, resource, time
from pathlib import Path
import numpy as np, soundfile as sf

ROOT = Path(__file__).resolve().parent
CHUNK = 1600  # 100 ms, as NowCaption's capture delivers it (--chunk-ms changes it)
TAIL = 1.5    # seconds of silence after each clip, as in live use

ABBR = {'mr': 'mister', 'mrs': 'missus', 'dr': 'doctor', 'st': 'saint'}
def words(s):
    return [ABBR.get(w, w) for w in re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", s.lower())]

def align(ref, hyp):
    """Levenshtein alignment; returns (errors, {ref_index: hyp_index} for exact matches)."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), dtype=np.int32); d[:, 0] = range(n + 1); d[0, :] = range(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i, j] = min(d[i-1, j] + 1, d[i, j-1] + 1, d[i-1, j-1] + (ref[i-1] != hyp[j-1]))
    i, j, match = n, m, {}
    while i and j:
        if ref[i-1] == hyp[j-1] and d[i, j] == d[i-1, j-1]: match[i-1] = j-1; i -= 1; j -= 1
        elif d[i, j] == d[i-1, j-1] + 1: i -= 1; j -= 1
        elif d[i, j] == d[i-1, j] + 1: i -= 1
        else: j -= 1
    return int(d[n, m]), match

class Sherpa:
    def __init__(self, path, threads, decoding='greedy_search'):
        import sherpa_onnx as so
        p = Path(path); f = lambda n: str(next(p.glob(n + '*.onnx')))
        self.r = so.OnlineRecognizer.from_transducer(
            tokens=str(p / 'tokens.txt'), encoder=f('encoder'), decoder=f('decoder'), joiner=f('joiner'),
            num_threads=threads, decoding_method=decoding)
    def start(self): self.s = self.r.create_stream()
    def feed(self, x):
        self.s.accept_waveform(16000, x.astype(np.float32) / 32768)
        while self.r.is_ready(self.s): self.r.decode_stream(self.s)
        return self.r.get_result(self.s)
    def finish(self):
        self.s.input_finished()
        while self.r.is_ready(self.s): self.r.decode_stream(self.s)
        return self.r.get_result(self.s)

class April:
    """April ASR in sync mode. APRIL_LIB names libaprilasr.so; otherwise the one a built
    Live Captions checkout (LIVECAPTIONS_DIR) contains is used."""
    LC = Path(os.environ.get('LIVECAPTIONS_DIR', 'LiveCaptions'))
    def __init__(self, path, threads, decoding=None):
        class Token(c.Structure):
            _fields_ = [('token', c.c_char_p), ('logprob', c.c_float), ('flags', c.c_int), ('time_ms', c.c_size_t), ('reserved', c.c_void_p)]
        self.CB = c.CFUNCTYPE(None, c.c_void_p, c.c_int, c.c_size_t, c.POINTER(Token))
        class Config(c.Structure):
            _fields_ = [('speaker', c.c_uint8 * 16), ('handler', self.CB), ('userdata', c.c_void_p), ('flags', c.c_int)]
        self.Config = Config
        L = self.L = c.CDLL(os.environ.get('APRIL_LIB') or str(self.LC / 'builddir/subprojects/april-asr/libaprilasr.so'))
        for name, args, res in [('aam_api_init', [c.c_int], None), ('aam_create_model', [c.c_char_p], c.c_void_p),
                                ('aas_create_session', [c.c_void_p, Config], c.c_void_p),
                                ('aas_feed_pcm16', [c.c_void_p, c.POINTER(c.c_short), c.c_size_t], None),
                                ('aas_flush', [c.c_void_p], None), ('aas_free', [c.c_void_p], None)]:
            fn = getattr(L, name); fn.argtypes = args; fn.restype = res
        L.aam_api_init(1)
        self.model = L.aam_create_model(os.fsencode(path or self.LC / 'april-english-dev-01110_en.april'))
        assert self.model, 'April model load failed'
    def start(self):
        self.done, self.partial = [], ''
        @self.CB
        def cb(_u, kind, count, toks):
            text = ''.join(toks[j].token.decode() for j in range(count))
            if kind == 2: self.done.append(text); self.partial = ''
            elif kind == 1: self.partial = text
        self.cb = cb
        cfg = self.Config(); cfg.handler = cb; cfg.flags = 0  # synchronous
        self.s = self.L.aas_create_session(self.model, cfg)
    def text(self): return ' '.join(self.done + [self.partial])
    def feed(self, x):
        x = np.ascontiguousarray(x, dtype=np.int16)
        self.L.aas_feed_pcm16(self.s, x.ctypes.data_as(c.POINTER(c.c_short)), len(x)); return self.text()
    def finish(self):
        self.L.aas_flush(self.s); t = self.text(); self.L.aas_free(self.s); return t

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('engine', choices=['sherpa', 'april'])
    ap.add_argument('--model', default='')
    ap.add_argument('--name', required=True)
    ap.add_argument('--cond', default='clean', choices=['clean', 'noisy'])
    ap.add_argument('--threads', type=int, default=2)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--chunk-ms', type=int, default=100, help='audio fed per step')
    ap.add_argument('--decoding', default='greedy_search', choices=['greedy_search', 'modified_beam_search'],
                    help='sherpa only')
    ap.add_argument('--data', default=str(ROOT / 'data' / 'dummy'), help='folder with refs.json and WAVs')
    a = ap.parse_args()
    os.environ['OMP_NUM_THREADS'] = str(a.threads)
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    eng = (Sherpa if a.engine == 'sherpa' else April)(a.model, a.threads, a.decoding)
    CHUNK = a.chunk_ms * 16
    D = Path(a.data)
    refs = json.loads((D / 'refs.json').read_text())[:a.limit]
    timing = json.loads((D / 'word_times.json').read_text()) if (D / 'word_times.json').exists() else {}
    rng = np.random.default_rng(0)
    tot_err = tot_words = 0; audio_s = cpu_s = 0.0; steps = []; first_lat, stable_lat = [], []; items = []
    for row in refs:
        x, sr = sf.read(D / row['audio'], dtype='int16'); assert sr == 16000
        if a.cond == 'noisy':  # white noise at 10 dB SNR
            sig = x.astype(np.float64); p = np.mean(sig ** 2)
            x = np.clip(sig + rng.normal(0, np.sqrt(p / 10), len(sig)), -32768, 32767).astype(np.int16)
        x = np.concatenate([x, np.zeros(int(TAIL * 16000), np.int16)])
        eng.start(); hist = []
        for off in range(0, len(x), CHUNK):
            t0 = time.perf_counter(); t0c = time.process_time()
            txt = eng.feed(x[off:off + CHUNK]) if off + CHUNK < len(x) else eng.finish()
            dt = time.perf_counter() - t0; cpu_s += time.process_time() - t0c; steps.append(dt * 1000)
            hist.append(((off + min(CHUNK, len(x) - off)) / 16000 + dt, words(txt)))
        audio_s += len(x) / 16000
        ref = words(row['text']); final = hist[-1][1]
        err, match = align(ref, final); tot_err += err; tot_words += len(ref)
        ends = timing.get(row['audio'], {})
        for ri, hi in match.items():
            if str(ri) not in ends: continue
            w, end = final[hi], ends[str(ri)]
            shown = [t for t, ws in hist if len(ws) > hi and ws[hi] == w]
            stable = next(t for k, (t, ws) in enumerate(hist) if all(len(v) > hi and v[hi] == w for _, v in hist[k:]))
            if shown: first_lat.append(shown[0] - end)
            stable_lat.append(stable - end)
        items.append({'audio': row['audio'], 'ref': row['text'], 'hyp': ' '.join(final), 'errors': err})
    pct = lambda v, q: round(float(np.percentile(v, q)), 3) if v else None
    out = {'name': a.name, 'data': D.name, 'cond': a.cond, 'threads': a.threads, 'wer': round(tot_err / tot_words, 4),
           'words': tot_words, 'errors': tot_err,
           'cpu_rtf': round(cpu_s / audio_s, 4), 'step_ms_p50': pct(steps, 50), 'step_ms_p99': pct(steps, 99),
           'first_shown_lag_s_p50': pct(first_lat, 50), 'first_shown_lag_s_p90': pct(first_lat, 90),
           'stable_lag_s_p50': pct(stable_lat, 50), 'stable_lag_s_p90': pct(stable_lat, 90), 'timed_words': len(stable_lat),
           'peak_rss_mb': round((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - 0 * rss0) / 1024), 'items': items}
    (ROOT / 'results').mkdir(exist_ok=True)
    (ROOT / f'results/{a.name}-{D.name}-{a.cond}-t{a.threads}.json').write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != 'items'}))

if __name__ == '__main__':
    main()
