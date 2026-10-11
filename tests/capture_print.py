"""Caption a PulseAudio source for N seconds and print the transcript (used by test_flatpak.sh)."""
import sys, time
from nowcaption import audio, engine

device, seconds = sys.argv[1], float(sys.argv[2])
cap = engine.Captioner(engine.load_recognizer(), engine.load_punctuation())
capture = audio.Capture(cap.feed, lambda e: print('capture error:', e, flush=True))
capture.start(device)
print('READY', flush=True)
time.sleep(seconds)
capture.stop()
cap.flush()
print(cap.transcript(), flush=True)
