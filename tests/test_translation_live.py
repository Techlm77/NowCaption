#!/usr/bin/env python3
"""Translation with the real models: Spanish, then Japanese after a pause, through the
recognition worker as the app runs it (no audio device; 2x real time).

    PYTHONPATH=src .venv/bin/python tests/test_translation_live.py

Needs tools/fetch-models.sh --translation. Checks that each language is identified, that
the English keeps the content of the sentence, and that turning translation off releases
its models. It also checks the captions as shown while the speech plays, not only the saved
transcript: English shows up during the speech, contains no other script, and is never
reworded except once per sentence, when the sentence is finished.
"""
import json, queue, re, sys, time, wave
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
    worker = engine.RecognitionWorker(lambda kind, value: events.put((time.monotonic(), kind, value)))
    captions, played = [], []
    worker.start()
    languages = []

    def wait(kind, pred=lambda v: True, timeout=60):
        end = time.monotonic() + timeout
        while True:
            t, k, v = events.get(timeout=max(0.01, end - time.monotonic()))
            if k == 'caption' and v[1] and (not captions or captions[-1][1] != v[1]):
                captions.append((t, v[1]))
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
            pcm = wave.open(str(HERE / clip['audio'])).readframes(10**9)
            played.append([time.monotonic(), None])
            pcm += bytes(16000 * 2 * 3)
            for i in range(0, len(pcm), 1280):
                worker.feed(worker.source_gen, pcm[i:i + 1280])
                time.sleep(0.02)
                if played[-1][1] is None and i >= len(pcm) - 16000 * 2 * 3:
                    played[-1][1] = time.monotonic()  # the speech has been played
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
        foreign = [text for _, text in captions if re.search(r'[^\x00-\u024f\u2000-\u206f€£°]', text)]
        live = [all(not (a <= t <= b) for t, _ in captions) for a, b in played]
        sentences = sum(1 for l in transcript.splitlines() if ' [' in l)
        rewrites = sum(not b.startswith(a) for (_, a), (_, b) in zip(captions, captions[1:]))
        print(f'captions: {len(captions)} shown, {rewrites} reworded for {sentences} finished sentences, '
              f'{len(foreign)} with another script, live during each clip: {[not x for x in live]}')
        ok = ok and not foreign and not any(live) and rewrites <= sentences
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
