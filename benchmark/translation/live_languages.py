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


def translate_clip(rec, tr, pcm, timeout=30.0):
    """Drain final sentences, with a deadline and an error path even if the thread dies."""
    errors, store = [], engine.TranscriptStore()
    tw = engine.TranslationWorker(tr, store, on_caption=lambda *a: None, on_error=errors.append)
    tw.start()
    try:
        cap = engine.TranslatingCaptioner(rec, tw, gen=lambda: 0, store=store)
        data = pcm.tobytes() + bytes(16000 * 2 * 2)
        for i in range(0, len(data), 3200):
            cap.feed(data[i:i + 3200])
        cap.flush()
        tw.close()  # no more input: exiting normally is the completion signal
        tw.join(timeout)
        if tw.is_alive():
            raise TimeoutError(f'Translation did not finish within {timeout:g} seconds')
        if errors:
            raise RuntimeError(f'Translation failed: {errors[0]}') from errors[0]
        if tw.pending():
            raise RuntimeError('Translation worker stopped with unfinished sentences')
        return ' '.join(line.split('] ', 1)[1].split(' [')[0] for line in store.lines())
    finally:
        tw.stop()
        tw.join(3)


def accuracy(base, folders, timeout=30.0):
    import sacrebleu
    rec, tr = engine.load_translation()
    for folder in folders:
        hyps, refs = [], []
        for r in clips(base, folder):
            try:
                pcm = read_wav(f'{base}/{folder}/{r["audio"]}')
                hyps.append(translate_clip(rec, tr, pcm, timeout))
            except Exception as exc:
                raise RuntimeError(f'{folder}/{r["audio"]}: {exc}') from exc
            refs.append(r['english'])
        print(json.dumps({'lang': code(folder), 'clips': len(refs),
                          'chrf': round(sacrebleu.corpus_chrf(hyps, [refs]).score, 2)}), flush=True)


def observe(event, log):
    t, kind, value = event
    if kind in ('load-error', 'error', 'translation-error', 'overload'):
        raise RuntimeError(f'{kind}: {value}')
    if kind == 'caption':
        log.append((t, value[1]))


def wait_event(events, kind, log, pred=lambda v: True, timeout=120.0):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f'Timed out waiting for {kind}')
        try:
            event = events.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError(f'Timed out waiting for {kind}') from exc
        observe(event, log)
        if event[1] == kind and pred(event[2]):
            return event


def drain(events, log):
    while True:
        try:
            event = events.get_nowait()
        except queue.Empty:
            return
        observe(event, log)


def score_clip(log, on, off, end, complete, reason=None):
    """Only changed, nonempty passages count; completion must be checked by the caller."""
    previous, changes = '', []
    for t, text in sorted(log):
        if t > end:
            break
        changed = text != previous
        previous = text
        if on <= t and changed and text.strip():
            changes.append(t)
    status = 'complete' if complete and changes else 'no_caption' if complete else 'unfinished'
    return {'status': status,
            'first_s': round(changes[0] - on, 2) if changes else None,
            'final_s': round(changes[-1] - off, 2) if status == 'complete' else None,
            'reason': reason or (None if status == 'complete' else status)}


def latency_summary(records):
    rows = []
    for lang in dict.fromkeys(r['lang'] for r in records):
        results = [r for r in records if r['lang'] == lang]
        first = [r['first_s'] for r in results if r['first_s'] is not None]
        final = [r['final_s'] for r in results if r['final_s'] is not None]
        completed = sum(r['status'] == 'complete' for r in results)
        rows.append({'lang': lang, 'scorer_version': 2, 'clips': len(results),
                     'completed': completed, 'failed': len(results) - completed,
                     'first_s': round(float(np.median(first)), 2) if first else None,
                     'final_s': round(float(np.median(final)), 2) if final else None,
                     'first_all': [r['first_s'] for r in results],
                     'final_all': [r['final_s'] for r in results], 'results': results})
    return rows


def latency(base, n, folders, timeout=30.0):
    events, log, records = queue.Queue(), [], []
    w = engine.RecognitionWorker(lambda k, v: events.put((time.monotonic(), k, v)))
    w.start()
    failure = None
    try:
        wait_event(events, 'ready', log)
        w.set_translation(True)
        wait_event(events, 'translation', log, lambda v: v is True)
        for folder in folders:
            for r in clips(base, folder, n):
                # Each clip has its own stream and passage; late work cannot be attributed
                # to the next language. A snapshot is a barrier after queued audio/commands.
                w.clear(len(records) + 1)
                w.snapshot(None)
                wait_event(events, 'snapshot', log, timeout=timeout)
                log.clear()
                pcm = read_wav(f'{base}/{folder}/{r["audio"]}')
                on, off = speech_span(pcm)
                data = pcm.tobytes() + bytes(16000 * 2 * 4)
                start = t_next = time.monotonic()
                complete, reason = False, None
                try:
                    for i in range(0, len(data), 1280):  # 40 ms, in real time
                        w.feed(w.source_gen, data[i:i + 1280])
                        t_next += 0.04
                        time.sleep(max(0, t_next - time.monotonic()))
                        drain(events, log)
                    w.snapshot(None)
                    wait_event(events, 'snapshot', log, timeout=timeout)
                    deadline = time.monotonic() + timeout
                    while True:
                        tw = w.translation_worker
                        if tw is None:
                            raise RuntimeError('Translation stopped before the clip completed')
                        with tw.lock:
                            complete = (w.captioner.flushed and not tw.waiting
                                        and not tw.sentence and not tw.words)
                            alive = tw.is_alive()
                        drain(events, log)
                        if complete:
                            break
                        if not alive:
                            raise RuntimeError('Translation worker stopped before the clip completed')
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f'Clip did not finish within {timeout:g} seconds after playback')
                        time.sleep(0.01)
                except (RuntimeError, TimeoutError) as exc:
                    complete, reason, failure = False, str(exc), exc
                result = score_clip(log, start + on, start + off, time.monotonic(), complete, reason)
                records.append({'lang': code(folder), 'clip': r['audio'], **result})
                if failure:
                    break
            if failure:
                break
    finally:
        w.stop()
        w.join_all(10)
    for row in latency_summary(records):
        print(json.dumps(row), flush=True)
    if failure:
        raise RuntimeError(f'Latency run failed: {failure}') from failure


if __name__ == '__main__':
    if sys.argv[1] == 'accuracy':
        accuracy(sys.argv[2], sys.argv[3:])
    else:
        latency(sys.argv[2], int(sys.argv[3]), sys.argv[4:])
