#!/usr/bin/env python3
"""End-to-end test: play fixtures into a private null sink and caption its monitor
through the same GStreamer capture and engine the app uses. Nothing is played aloud.

    PYTHONPATH=src .venv/bin/python tests/test_live.py
"""
import json, os, queue, subprocess, sys, time
from pathlib import Path

from nowcaption import audio, engine
from capture_print import wait_for
from transcript_check import transcript_words, words, wer

HERE = Path(__file__).parent / 'fixtures'
SINK = f'nowcaption_test_{os.getpid()}'


def main():
    mod = subprocess.check_output(['pactl', 'load-module', 'module-null-sink',
                                   f'sink_name={SINK}']).decode().strip()
    capture = worker = None
    try:
        events, updates = queue.Queue(), []
        def on_event(kind, value):
            if kind == 'caption':
                updates.append(time.monotonic())
            events.put((kind, value))
        worker = engine.RecognitionWorker(on_event)
        worker.start()
        assert wait_for(events, 'ready', 60) is None
        capture = audio.Capture(worker.feed,
            on_started=lambda gen: events.put(('capture-ready', gen)),
            on_ended=lambda gen, reason: events.put(('capture-error', reason)))
        worker.set_source(1)
        capture.start(f'{SINK}.monitor', 1)
        wait_for(events, 'capture-ready', 20)
        refs = json.loads((HERE / 'refs.json').read_text())
        for r in refs:
            subprocess.run(['paplay', f'--device={SINK}', str(HERE / r['audio'])], check=True, timeout=30)
        time.sleep(2.5)
        capture.stop()
        if not capture.wait_stopped(5):
            raise TimeoutError('Audio capture did not stop')
        worker.set_source(2)  # flush the last words
        worker.snapshot(lambda text: None)
        transcript = wait_for(events, 'snapshot', 10)[1]
        worker.stop()
        score = wer([w for r in refs for w in words(r['text'])], transcript_words(transcript))
        print(transcript)
        print(f'WER {score:.3f}, {len(updates)} caption updates')
        ok = score <= 0.15 and bool(updates)
        print('PASS' if ok else 'FAIL')
        return 0 if ok else 1
    finally:
        if capture:
            capture.stop()
        if worker:
            worker.stop()
            worker.join(2)
        subprocess.run(['pactl', 'unload-module', mod])


if __name__ == '__main__':
    sys.exit(main())
