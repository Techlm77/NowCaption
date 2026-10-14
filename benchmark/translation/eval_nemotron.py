"""Stage 1 of the cascade: Nemotron 3.5 streaming ASR (auto language) on FLEURS clips,
fed in 40 ms blocks like the app. Writes the source-language transcripts and timing.
Usage: eval_nemotron.py <fleurs-dir> <model-dir> <threads> <out.json> <lang>...
"""
import json, os, sys, time
import numpy as np
import soundfile as sf
import sherpa_onnx as so

fleurs, mdir, threads, out, langs = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5:]
rec = so.OnlineRecognizer.from_transducer(
    tokens=f'{mdir}/tokens.txt', encoder=f'{mdir}/encoder.int8.onnx', decoder=f'{mdir}/decoder.int8.onnx',
    joiner=f'{mdir}/joiner.int8.onnx', num_threads=threads, decoding_method='greedy_search')
results = {}
for lang in langs:
    refs = json.load(open(os.path.join(fleurs, lang, 'refs.json')))
    items, compute, audio = [], 0.0, 0.0
    for r in refs:
        x, rate = sf.read(os.path.join(fleurs, lang, r['audio']), dtype='float32')
        x = np.concatenate([x, np.zeros(int(rate * 1.5), np.float32)])  # trailing silence flushes the last words
        s = rec.create_stream()
        s.set_option('language', 'auto')
        t = time.perf_counter()
        for i in range(0, len(x), 640):
            s.accept_waveform(rate, x[i:i + 640])
            while rec.is_ready(s):
                rec.decode_stream(s)
        compute += time.perf_counter() - t
        audio += len(x) / rate
        items.append({'audio': r['audio'], 'source': r['source'], 'english': r['english'],
                      'asr': ''.join(rec.recognizer.get_result(s).tokens).strip()})
    results[lang] = {'rtf': round(compute / audio, 3), 'items': items}
    print(lang, 'rtf', results[lang]['rtf'], '|', items[0]['asr'][:90], flush=True)
json.dump(results, open(out, 'w'), ensure_ascii=False, indent=1)
