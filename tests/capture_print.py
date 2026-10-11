"""Caption a PulseAudio source for N seconds and print the transcript (used by test_flatpak.sh)."""
import queue
import json
import sys
import time

from nowcaption import audio, engine

def emit(kind, **fields):
    print(json.dumps(dict(kind=kind, **fields)), flush=True)


def get_event(events, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Timed out waiting for capture or recognition')
    try:
        kind, value = events.get(timeout=remaining)
    except queue.Empty as e:
        raise TimeoutError('Timed out waiting for capture or recognition') from e
    if kind in ('load-error', 'error', 'capture-error', 'overload'):
        raise RuntimeError(f'{kind}: {value}')
    if kind == 'warning':
        emit('warning', message=value)
    return kind, value


def wait_for(events, expected, seconds):
    end = time.monotonic() + seconds
    while True:
        kind, value = get_event(events, end)
        if kind == expected:
            return value


def main():
    device, seconds = sys.argv[1], float(sys.argv[2])
    events, capture = queue.Queue(), None
    worker = engine.RecognitionWorker(lambda kind, value: events.put((kind, value)))
    worker.start()
    try:
        wait_for(events, 'ready', 60)
        capture = audio.Capture(worker.feed,
            on_started=lambda gen: events.put(('capture-ready', gen)),
            on_ended=lambda gen, reason: events.put(('capture-error', reason)))
        worker.set_source(1)
        capture.start(device, 1)
        wait_for(events, 'capture-ready', 20)
        print('READY', flush=True)  # actual samples arrived from the chosen device
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                get_event(events, min(end, time.monotonic() + 0.25))
            except TimeoutError:
                pass
        capture.stop()
        if not capture.wait_stopped(5):
            raise TimeoutError('Audio capture did not stop')
        worker.set_source(2)  # commits the final words, then ends that acoustic stream
        worker.snapshot(None)
        emit('transcript', text=wait_for(events, 'snapshot', 10)[1])
        return 0
    except Exception as e:
        emit('error', message=str(e))
        return 1
    finally:
        if capture:
            capture.stop()
        worker.stop()
        worker.join(2)


if __name__ == '__main__':
    sys.exit(main())
