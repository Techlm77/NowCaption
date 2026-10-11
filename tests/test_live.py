#!/usr/bin/env python3
"""End-to-end test: play fixtures into a private null sink and caption its monitor
through the same GStreamer capture and engine the app uses. Nothing is played aloud.

    PYTHONPATH=src .venv/bin/python tests/test_live.py
"""
import json, re, subprocess, sys, threading, time
from pathlib import Path

from nowcaption import audio, engine

HERE = Path(__file__).parent / 'fixtures'


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
    mod = subprocess.check_output(['pactl', 'load-module', 'module-null-sink', 'sink_name=nowcaption_test']).decode().strip()
    try:
        cap = engine.Captioner(engine.load_recognizer(), engine.load_punctuation())
        updates = []
        cap.on_update = lambda lines, partial: updates.append(time.monotonic())
        capture = audio.Capture(cap.feed, lambda e: print('capture error:', e))
        capture.start('nowcaption_test.monitor')
        time.sleep(1)
        refs = json.loads((HERE / 'refs.json').read_text())
        for r in refs:
            subprocess.run(['paplay', '--device=nowcaption_test', str(HERE / r['audio'])], check=True)
        time.sleep(2.5)
        capture.stop()
        text = ' '.join(t for _, t in cap.lines)
        score = wer([w for r in refs for w in words(r['text'])], words(text))
        print(cap.transcript())
        print(f'WER {score:.3f}, {len(updates)} caption updates')
        ok = score < 0.15 and any(c in text for c in '.,') and updates
        print('PASS' if ok else 'FAIL')
        return 0 if ok else 1
    finally:
        subprocess.run(['pactl', 'unload-module', mod])


if __name__ == '__main__':
    sys.exit(main())
