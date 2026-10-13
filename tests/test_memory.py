#!/usr/bin/env python3
"""Memory test with the real models: caption looped speech for several minutes of audio,
as fast as the CPU allows, and fail if memory keeps growing. No audio device is used.

    PYTHONPATH=src .venv/bin/python tests/test_memory.py [audio-minutes] [--filter]

1.1.2 and earlier grew by 4-14 MB per audio minute (sherpa-onnx leaked result.text).
Since 1.1.3 growth is a few KB per audio minute, until the recognizer's text resets at
20,000 characters (about 25 minutes of speech); since 1.1.4 the transcript itself is in
a temporary file. It takes about 10 s per audio minute.
"""
import ctypes, sys, time, wave
from pathlib import Path

from nowcaption import engine

HERE = Path(__file__).parent / 'fixtures'
BLOCK = 1280  # 40 ms, as the app's capture delivers it
LIMIT_MB_PER_MIN = 1.0


def anon_mb():
    """Anonymous resident memory after returning freed heap to the system."""
    try:
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except (OSError, AttributeError):
        pass
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('RssAnon:'):
            return int(line.split()[1]) / 1024
    raise RuntimeError('RssAnon is not available')


def main():
    minutes = float(next((a for a in sys.argv[1:] if not a.startswith('--')), 8))
    if minutes < 2:
        sys.exit('Use at least 2 audio minutes: the first one is warm-up.')
    rec, punct = engine.load_models()
    cap = engine.Captioner(rec, punct)
    if '--filter' in sys.argv:
        cap.set_music_filter(engine.load_music_filter())
    pause = bytes(int(engine.RATE * 1.5) * 2)
    loop = b''.join(wave.open(str(f)).readframes(10**9) + pause for f in sorted(HERE.glob('*.wav')))
    blocks = [loop[i:i + BLOCK] for i in range(0, len(loop) - BLOCK + 1, BLOCK)]
    per_minute = int(60 / 0.04)
    t0 = time.monotonic()
    for i in range(per_minute):  # a minute of warm-up
        cap.feed(blocks[i % len(blocks)])
    start = anon_mb()
    for i in range(per_minute, int(minutes * per_minute)):
        cap.feed(blocks[i % len(blocks)])
    end = anon_mb()
    rate = (end - start) / (minutes - 1)
    print(f'{minutes:g} audio minutes in {time.monotonic() - t0:.0f} s, {cap.count} lines; '
          f'memory {start:.1f} -> {end:.1f} MB, {rate * 1024:+.1f} KB per audio minute')
    ok = rate < LIMIT_MB_PER_MIN
    print('PASS' if ok else f'FAIL: more than {LIMIT_MB_PER_MIN} MB per audio minute')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
