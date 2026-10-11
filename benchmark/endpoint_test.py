"""WER of continuous audio (clips joined with 0.4 s gaps) with different endpoint/reset settings."""
import json, sys, numpy as np, soundfile as sf, sherpa_onnx as so
from pathlib import Path
from bench import words, align
m = Path(sys.argv[1]); f = lambda n: str(next(m.glob(n + '*.onnx')))
refs = json.load(open('data/refs.json'))[:30]
gap = np.zeros(6400, np.float32)
x = np.concatenate([np.concatenate([sf.read('data/' + r['audio'], dtype='float32')[0], gap]) for r in refs])
ref = [w for r in refs for w in words(r['text'])]
for label, kw in [('no endpoint', dict(enable_endpoint_detection=False)),
                  ('rule2 0.8s', dict(enable_endpoint_detection=True, rule2_min_trailing_silence=0.8)),
                  ('rule2 1.2s', dict(enable_endpoint_detection=True, rule2_min_trailing_silence=1.2)),
                  ('rule2 0.8s keep-state', dict(enable_endpoint_detection=True, rule2_min_trailing_silence=0.8, reset_encoder=False))]:
    r = so.OnlineRecognizer.from_transducer(tokens=str(m/'tokens.txt'), encoder=f('encoder'), decoder=f('decoder'), joiner=f('joiner'), num_threads=1, rule3_min_utterance_length=20, **kw)
    s = r.create_stream(); out = []; resets = 0
    for o in range(0, len(x), 1600):
        s.accept_waveform(16000, x[o:o+1600])
        while r.is_ready(s): r.decode_stream(s)
        if kw['enable_endpoint_detection'] and r.is_endpoint(s):
            out.append(r.get_result(s)); r.reset(s); resets += 1
    s.accept_waveform(16000, np.zeros(16000, np.float32)); s.input_finished()
    while r.is_ready(s): r.decode_stream(s)
    out.append(r.get_result(s))
    err, _ = align(ref, words(' '.join(out)))
    print(f'{label:24s} WER {err/len(ref):.4f}  resets {resets}')
