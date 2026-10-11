"""Reference word end times from offline Parakeet TDT timestamps, kept only where it matches the reference word."""
import json, sys
from pathlib import Path
import numpy as np, soundfile as sf, sherpa_onnx as so
sys.path.insert(0, str(Path(__file__).parent)); from bench import words, align
m = Path('models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8')
r = so.OfflineRecognizer.from_transducer(encoder=str(m/'encoder.int8.onnx'), decoder=str(m/'decoder.int8.onnx'),
    joiner=str(m/'joiner.int8.onnx'), tokens=str(m/'tokens.txt'), model_type='nemo_transducer', num_threads=8)
out = {}; kept = total = errs = 0
for row in json.load(open('data/refs.json')):
    x, _ = sf.read('data/' + row['audio'], dtype='float32'); s = r.create_stream(); s.accept_waveform(16000, x); r.decode_stream(s)
    res = s.result; toks, ts = res.tokens, res.timestamps
    durs = list(getattr(res, 'durations', []) or [])
    # group BPE tokens ('▁' starts a word) into words with end times
    wl, we = [], []
    for k, t in enumerate(toks):
        end = ts[k] + (durs[k] if durs else 0.08)
        if t.startswith(' ') or t.startswith('▁') or not wl: wl.append(t); we.append(end)
        else: wl[-1] += t; we[-1] = end
    hyp, times = [], []
    for w, e in zip(wl, we):
        for n in words(w): hyp.append(n); times.append(e)
    ref = words(row['text']); err, match = align(ref, hyp); errs += err; total += len(ref)
    out[row['audio']] = {str(ri): round(times[hi], 3) for ri, hi in match.items()}; kept += len(match)
json.dump(out, open('data/word_times.json', 'w'))
print(f'parakeet-tdt offline WER {errs/total:.4f}; timed {kept}/{total} words; has durations: {bool(durs)}')
