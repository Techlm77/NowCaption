"""Caption a PulseAudio source for N seconds and print the transcript (used by test_flatpak.sh)."""
import queue
import sys
import time

from nowcaption import audio, engine

device, seconds = sys.argv[1], float(sys.argv[2])
events = queue.Queue()
worker = engine.RecognitionWorker(lambda kind, value: events.put((kind, value)))
worker.start()
kind, value = events.get(timeout=60)
if kind != 'ready':
    sys.exit(f'model load failed: {value}')
capture = audio.Capture(worker.feed, on_ended=lambda gen, reason: print('capture ended:', reason, flush=True))
worker.set_source(1)
capture.start(device, 1)
print('READY', flush=True)
time.sleep(seconds)
capture.stop()
worker.set_source(2)  # flushes the last words
worker.snapshot(lambda text: None)
while True:
    kind, value = events.get(timeout=10)
    if kind == 'snapshot':
        print(value[1], flush=True)
        break
worker.stop()
