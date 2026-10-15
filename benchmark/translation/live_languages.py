"""Translate to English as people use it, per language, with NowCaption's own code.

accuracy: each FLEURS test clip through Nemotron and NowCaption's live translation (sentence
splitting, sections, guards); the English it finally shows is scored with chrF against the
FLEURS English reference.
latency: clips played in real time through the recognition worker with translation on and
the language detected, as the app runs; per clip, when the first English shows (from when
the speech starts) and when the clip's English is complete (from when the speech ends).

    PYTHONPATH=src python benchmark/translation/live_languages.py accuracy <fleurs-dir> <lang>...
    PYTHONPATH=src python benchmark/translation/live_languages.py latency <fleurs-dir> <clips> <lang>...

<fleurs-dir> is what translation/fetch_fleurs.py writes; <lang> its folder names (es_419 ...).
Needs tools/fetch-models.sh --translation, sacrebleu for accuracy, and a CPU that is otherwise
idle for latency. Prints one JSON line per language.
"""
import json, queue, struct, sys, time
import numpy as np
from nowcaption import engine


def code(folder):
    return {'cmn': 'zh', 'nb': 'no'}.get(folder.split('_')[0], folder[:2])


def read_wav(path):
    """Mono 16 kHz WAV, 16-bit or float, as 16-bit samples."""
    b = open(path, 'rb').read()
    i, fmt, data = 12, None, None
    while i < len(b):
        cid, size = b[i:i + 4], struct.unpack('<I', b[i + 4:i + 8])[0]
        if cid == b'fmt ':
            fmt = struct.unpack('<HHIIHH', b[i + 8:i + 24])
        if cid == b'data':
            data = b[i + 8:i + 8 + size]
            break
        i += 8 + size + (size & 1)
    assert fmt[1] == 1 and fmt[2] == 16000, fmt
    x = np.frombuffer(data, np.float32) if fmt[0] == 3 else np.frombuffer(data, np.int16).astype(np.float32) / 32768
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def speech_span(pcm):
    """Seconds from the start of the clip to its first and past its last 20 ms of speech."""
    x = pcm.astype(np.float32)
    n = len(x) // 320
    e = np.sqrt((x[:n * 320].reshape(n, 320) ** 2).mean(1))
    on = np.where(e > e.max() * 0.1)[0]
    return on[0] * 0.02, (on[-1] + 1) * 0.02


def clips(base, folder, n=None):
    return json.load(open(f'{base}/{folder}/refs.json'))[:n]


def accuracy(base, folders):
    import sacrebleu
    rec, tr = engine.load_translation()
    for folder in folders:
        hyps, refs = [], []
        for r in clips(base, folder):
            pcm = read_wav(f'{base}/{folder}/{r["audio"]}').tobytes() + bytes(16000 * 2 * 2)
            store = engine.TranscriptStore()
            tw = engine.TranslationWorker(tr, store, on_caption=lambda *a: None)
            tw.start()
            cap = engine.TranslatingCaptioner(rec, tw, gen=lambda: 0, store=store)
            for i in range(0, len(pcm), 3200):
                cap.feed(pcm[i:i + 3200])
            cap.flush()
            quiet_since, last = time.monotonic(), None
            while time.monotonic() - quiet_since < 0.4:  # until every sentence is translated
                with tw.lock:
                    state = (len(tw.waiting), tw.sentence, store.count)
                if state != last or state[0] or state[1]:
                    last, quiet_since = state, time.monotonic()
                time.sleep(0.02)
            hyps.append(' '.join(l.split('] ', 1)[1].split(' [')[0] for l in store.lines()))
            refs.append(r['english'])
            tw.stop()
            tw.join(3)
        print(json.dumps({'lang': code(folder), 'clips': len(refs),
                          'chrf': round(sacrebleu.corpus_chrf(hyps, [refs]).score, 2)}), flush=True)


def latency(base, n, folders):
    events = queue.Queue()
    w = engine.RecognitionWorker(lambda k, v: events.put((time.monotonic(), k, v)))
    w.start()

    def wait(kind, pred=lambda v: True):
        while True:
            t, k, v = events.get(timeout=120)
            if k == kind and pred(v):
                return

    wait('ready')
    w.set_translation(True)
    wait('translation', lambda v: v is True)
    time.sleep(1)
    log, plays = [], []

    def drain():
        while not events.empty():
            t, k, v = events.get()
            if k == 'caption':
                log.append((t, v[1]))
            if k == 'overload':
                print('OVERLOAD', v, flush=True)
    for folder in folders:
        for r in clips(base, folder, n):
            pcm = read_wav(f'{base}/{folder}/{r["audio"]}')
            on, off = speech_span(pcm)
            data = pcm.tobytes() + bytes(16000 * 2 * 4)  # then 4 s of silence
            start = t_next = time.monotonic()
            for i in range(0, len(data), 1280):  # 40 ms at a time, in real time
                w.feed(w.source_gen, data[i:i + 1280])
                t_next += 0.04
                time.sleep(max(0, t_next - time.monotonic()))
                if i % 64000 == 0:
                    drain()
            drain()
            plays.append((code(folder), start + on, start + off, time.monotonic()))
    w.stop()
    w.join_all(10)
    found = {}
    for lang, on, off, end in plays:
        before = [text for t, text in log if t <= on]
        changes = [(t, text) for t, text in log if on < t <= end and text != (before[-1] if before else '')]
        if changes:
            found.setdefault(lang, []).append((changes[0][0] - on, changes[-1][0] - off))
    for lang in dict.fromkeys(lang for lang, *_ in plays):
        v = found.get(lang, [])
        print(json.dumps({'lang': lang, 'clips': len(v),
                          'first_s': round(float(np.median([a for a, _ in v])), 2) if v else None,
                          'final_s': round(float(np.median([b for _, b in v])), 2) if v else None,
                          'first_all': [round(a, 2) for a, _ in v], 'final_all': [round(b, 2) for _, b in v]}), flush=True)


if __name__ == '__main__':
    if sys.argv[1] == 'accuracy':
        accuracy(sys.argv[2], sys.argv[3:])
    else:
        latency(sys.argv[2], int(sys.argv[3]), sys.argv[4:])
