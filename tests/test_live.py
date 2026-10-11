#!/usr/bin/env python3
"""End-to-end test: play fixtures into a private null sink and caption its monitor
through the same GStreamer capture and engine the app uses. Nothing is played aloud.

    PYTHONPATH=src .venv/bin/python tests/test_live.py
"""
import json, os, queue, re, subprocess, sys, time
from pathlib import Path

from nowcaption import audio, engine

HERE = Path(__file__).parent / 'fixtures'
SINK = f'nowcaption_test_{os.getpid()}'


def words(s):
    abbr = {'mr': 'mister', 'mrs': 'missus', 'dr': 'doctor'}
    return [abbr.get(w, w) for w in re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", s.lower())]


def wer(ref, hyp):
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        new = [i]
        for j, h in enumerate(hyp, 1):
            new.append(min(new[-1] + 1, row[j] + 1, row[j - 1] + (r != h)))
        row = new
    return row[-1] / len(ref)


def main():
    mod = subprocess.check_output(['pactl', 'load-module', 'module-null-sink',
                                   f'sink_name={SINK}']).decode().strip()
    try:
        events, updates = queue.Queue(), []
        def on_event(kind, value):
            if kind == 'caption':
                updates.append(time.monotonic())
            events.put((kind, value))
        worker = engine.RecognitionWorker(on_event)
        worker.start()
        assert wait_for(events, 'ready', 60) is None
        capture = audio.Capture(worker.feed, on_ended=lambda gen, reason: print('capture ended:', reason))
        worker.set_source(1)
        capture.start(f'{SINK}.monitor', 1)
        time.sleep(1)
        refs = json.loads((HERE / 'refs.json').read_text())
        for r in refs:
            subprocess.run(['paplay', f'--device={SINK}', str(HERE / r['audio'])], check=True)
        time.sleep(2.5)
        capture.stop()
        worker.set_source(2)  # flush the last words
        worker.snapshot(lambda text: None)
        transcript = wait_for(events, 'snapshot', 10)[1]
        worker.stop()
        text = ' '.join(line.split('] ', 1)[-1] for line in transcript.splitlines())
        score = wer([w for r in refs for w in words(r['text'])], words(text))
        print(transcript)
        print(f'WER {score:.3f}, {len(updates)} caption updates')
        ok = score < 0.15 and any(c in text for c in '.,') and updates
        print('PASS' if ok else 'FAIL')
        return 0 if ok else 1
    finally:
        subprocess.run(['pactl', 'unload-module', mod])


def wait_for(events, kind, timeout):
    end = time.monotonic() + timeout
    while True:
        k, v = events.get(timeout=max(0.1, end - time.monotonic()))
        if k == kind:
            return v
        if k in ('load-error', 'error'):
            raise SystemExit(f'{k}: {v}')


if __name__ == '__main__':
    sys.exit(main())
