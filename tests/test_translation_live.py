#!/usr/bin/env python3
"""Translation with the real models: Spanish, then Japanese after a pause, through the
recognition worker as the app runs it (no audio device; 2x real time).

    PYTHONPATH=src .venv/bin/python tests/test_translation_live.py

Needs tools/fetch-models.sh --translation. Checks that each language is identified, that
the English keeps the content of the sentence, and that turning translation off releases
its models.
"""
import json, queue, sys, time, wave
from pathlib import Path

from nowcaption import engine

HERE = Path(__file__).parent / 'fixtures'
KEYWORDS = {'fleurs-es-419.wav': ('es', ['funeral', 'people']),
            'fleurs-ja-jp.wav': ('ja', ['official language', 'catal'])}


def main():
    missing = engine.translation_missing()
    if missing:
        print('SKIP:', missing)
        return 0
    events = queue.Queue()
    worker = engine.RecognitionWorker(lambda kind, value: events.put((kind, value)))
    worker.start()
    languages = []

    def wait(kind, pred=lambda v: True, timeout=60):
        end = time.monotonic() + timeout
        while True:
            k, v = events.get(timeout=max(0.01, end - time.monotonic()))
            if k in ('load-error', 'error', 'translation-error', 'overload'):
                raise RuntimeError(f'{k}: {v}')
            if k == 'translation-language':
                languages.append(v)
            if k == kind and pred(v):
                return v

    try:
        wait('ready')
        started = time.monotonic()
        worker.set_translation(True)
        wait('translation', lambda v: v is True)
        print(f'translation loaded in {time.monotonic() - started:.1f} s')
        for clip in json.loads((HERE / 'translation.json').read_text()):
            pcm = wave.open(str(HERE / clip['audio'])).readframes(10**9) + bytes(16000 * 2 * 3)
            for i in range(0, len(pcm), 1280):
                worker.feed(worker.source_gen, pcm[i:i + 1280])
                time.sleep(0.02)
        time.sleep(3)
        worker.snapshot(lambda text: None)
        transcript = wait('snapshot')[1]
        print(transcript)
        ok = languages[-2:] == ['es', 'ja'] or languages == ['es', 'ja']
        for clip in json.loads((HERE / 'translation.json').read_text()):
            lang, words = KEYWORDS[clip['audio']]
            line = next((l for l in transcript.splitlines() if f'[{lang}: ' in l), '')
            english = line.split('] ', 1)[-1].split(f' [{lang}: ')[0].lower()
            found = all(w in english for w in words)
            print(f'{lang}: {"ok" if found else "MISSING " + str(words)} | {english}')
            ok = ok and found
        worker.set_translation(False)
        wait('translation', lambda v: v is False)
        ok = ok and worker.translation is None and type(worker.captioner) is engine.Captioner
        print('languages identified:', languages)
        print('PASS' if ok else 'FAIL')
        return 0 if ok else 1
    finally:
        worker.stop()
        worker.join_all(10)


if __name__ == '__main__':
    sys.exit(main())
