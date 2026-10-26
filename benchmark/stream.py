"""Streaming benchmark: one engine captions a whole test set as one live stream, as an app would.

Used for NowCaption 1.2.0's model choice (v3 against v2, on the ESB sets from get-esb.py) and for the
comparison with the caption engines other Linux apps use:

    python stream.py app:../models/nowcaption-fastconformer-en-80ms-cc-by-v3 --data data/esb/ami --name v3-cont
    python stream.py appreset:../models/nowcaption-fastconformer-en-80ms-cc-by-v3 --data data/esb/ami --name v3-fresh
    python stream.py moonshine:medium --data data/esb/ami [--interval 0.5]       (needs moonshine-voice)
    python stream.py april:april/april-english-dev-01110_en.april --data data/esb/ami    (build-april.sh)
    python stream.py vosk:vosk-model-small-en-us-0.15 --data data/esb/ami         (needs vosk)
    python stream.py whisper:whisper-small.en --data data/esb/ami [--threads 4]  (needs faster-whisper)

app: is continuous use; appreset: is the same session with the stream reset in every pause and primed with
the app's warm-up silence, so each clip starts on a fresh stream as a new stream does in the app.

As benchmark/pipeline.py measures NowCaption: the set's clips are joined by 2.0-2.1 s of digital
silence (seeded) and fed in 40 ms blocks. Each caption update is stamped with the time the app would
show it: when its audio arrived, or when the engine finished the work before it if it was still busy,
plus the compute time. Per reference word (timed by word_times.py), "first shown" is when it
first appeared at its final place, minus when it ends in the audio; "settled" is when it stopped
changing (the same for engines that never rewrite words). "shown" and "stable" are the same without
the position test: a word counts as shown when it is on screen and lines up with the final line,
since an engine that corrects an earlier word shifts every word after it.

Engines:
- app:FOLDER   a sherpa-onnx streaming transducer run by NowCaption's own caption pipeline
               (nowcaption.engine.Captioner: punctuation, line splitting, silence handling), 1 thread.
- moonshine:small|medium  Moonshine Streaming through moonshine-voice's own stream API (its VAD,
               lines and update cadence; --interval is its update_interval, default 0.5 s as shipped).
- april:FILE   april-asr, LiveCaptions' engine, with the model LiveCaptions ships: finished lines plus
               the current partial line, as LiveCaptions shows them.
- vosk:FOLDER  Vosk (Kaldi), as Speech Note, Kdenlive and nerd-dictation use it: results plus partial.
- whisper:FOLDER  faster-whisper with LocalAgreement-2 streaming (ufal/whisper_streaming, the policy
               WhisperLiveKit and the Whisper caption apps build on): every --min-chunk seconds of new
               audio (or later, if still busy) the buffer is transcribed again; words two passes agree
               on are committed. The uncommitted tail is shown too, as WhisperLiveKit shows it.

Errors use Whisper's English normaliser per clip, as on the Open ASR Leaderboard. CPU is the whole
process's CPU time (all threads) per second of audio while feeding; RAM is peak resident memory.
Results go to results/stream/<name>-<set>-s<seed>.json.
"""
import argparse
import json
import os
import random
import re
import resource
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent
BLOCK = 640   # 40 ms, as NowCaption captures audio
TAIL = 2.5    # seconds of silence after the last clip
RATE = 16000

ABBR = {'mr': 'mister', 'mrs': 'missus', 'dr': 'doctor', 'st': 'saint'}


def words(s):
    """benchmark/bench.py's word split (lower case, digits kept, apostrophes inside words)."""
    return [ABBR.get(w, w) for w in re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", s.lower())]


def align(ref, hyp):
    """Levenshtein alignment; (errors, {ref index: hyp index} for exact matches). As benchmark/bench.py."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), dtype=np.int32)
    d[:, 0] = range(n + 1)
    d[0, :] = range(m + 1)
    for i in range(1, n + 1):
        ri = ref[i - 1]
        for j in range(1, m + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (ri != hyp[j - 1]))
    i, j, match = n, m, {}
    while i and j:
        if ref[i - 1] == hyp[j - 1] and d[i, j] == d[i - 1, j - 1]:
            match[i - 1] = j - 1; i -= 1; j -= 1
        elif d[i, j] == d[i - 1, j - 1] + 1:
            i -= 1; j -= 1
        elif d[i, j] == d[i - 1, j] + 1:
            i -= 1
        else:
            j -= 1
    return int(d[n, m]), match


def layout(D, refs, seed, gap):
    """Clip start times in the session (s) and its length in samples, as pipeline.py lays it out."""
    rng = random.Random(seed)
    n = rng.randrange(0, BLOCK)
    lead, starts, gaps = n, [], []
    for row in refs:
        frames = sf.info(D / row['audio']).frames
        starts.append(n / RATE)
        n += frames
        g = int(rng.uniform(*gap) * RATE)
        gaps.append(g)
        n += g
    return lead, starts, gaps, n + int(TAIL * RATE)


def blocks(D, refs, lead, gaps):
    """The session as int16 40 ms blocks, read clip by clip (so a long set never sits in memory)."""
    def pieces():
        yield np.zeros(lead, np.int16)
        for row, g in zip(refs, gaps):
            x, sr = sf.read(D / row['audio'], dtype='int16')
            assert sr == RATE, row['audio']
            yield x
            yield np.zeros(g, np.int16)
        yield np.zeros(int(TAIL * RATE), np.int16)
    buf = np.zeros(0, np.int16)
    for p in pieces():
        buf = np.concatenate([buf, p])
        while len(buf) >= BLOCK:
            yield buf[:BLOCK]
            buf = buf[BLOCK:]
    if len(buf):
        yield buf


class App:
    """A sherpa-onnx transducer folder run through NowCaption's Captioner, as the app runs it."""
    HIDDEN = {'<EOU>', '<EOB>'}  # Parakeet Realtime EOU's end-of-utterance tokens are not words

    def __init__(self, folder, threads=1):
        import sherpa_onnx as so
        os.environ.setdefault('NOWCAPTION_MODELS', str(ROOT.parent / 'models'))
        sys.path.insert(0, os.environ.get('NOWCAPTION_SRC', str(ROOT.parent / 'src')))
        from nowcaption import engine  # works under python -I too, which ignores PYTHONPATH
        self.engine = engine
        p = Path(folder)
        feat = 80
        try:
            import onnx  # the export records the model's feature size (Parakeet EOU uses 128)
            meta = {m.key: m.value for m in onnx.load(str(p / 'encoder.int8.onnx'), load_external_data=False).metadata_props}
            feat = int(meta.get('feat_dim', 80))
        except ImportError:
            pass
        hidden = self.HIDDEN

        class Recognizer(so.OnlineRecognizer):
            def get_result(self, s):  # as engine.load_recognizer, minus the EOU tokens
                return ''.join(t for t in self.recognizer.get_result(s).tokens if t not in hidden).strip()

        class EouRecognizer(Recognizer):
            """Parakeet Realtime EOU emits nothing after its first <EOU> until its decoder is reset
            (it was trained one utterance at a time). So, as an app using it would: when <EOU>
            comes, keep the text so far and reset the stream (sherpa-onnx resets the encoder
            caches too), and show the kept text in front of the new words."""
            kept = {}  # from_transducer builds the object without __init__; one recognizer per process

            def decode_stream(self, s):
                super().decode_stream(s)
                toks = self.recognizer.get_result(s).tokens
                if toks and toks[-1] == '<EOU>':
                    self.kept[id(s)] = (self.kept.get(id(s), '') + ' ' + super().get_result(s)).strip()
                    so.OnlineRecognizer.reset(self, s)

            def get_result(self, s):
                return (self.kept.get(id(s), '') + ' ' + super().get_result(s)).strip()

            def reset(self, s):  # the Captioner's own reset (at 20,000 characters) drops the kept text too
                self.kept.pop(id(s), None)
                super().reset(s)

        eou = '<EOU>' in (p / 'tokens.txt').read_text().split()
        self.rec = (EouRecognizer if eou else Recognizer).from_transducer(
            tokens=str(p / 'tokens.txt'), encoder=str(p / 'encoder.int8.onnx'),
            decoder=str(p / 'decoder.int8.onnx'), joiner=str(p / 'joiner.int8.onnx'),
            num_threads=threads, decoding_method='greedy_search', feature_dim=feat)
        self.punct = engine.load_punctuation()
        self.events, self.clock = [], {}
        self.cap = engine.Captioner(self.rec, self.punct, on_update=self._update, keep_raw=True,
                                    store=engine.TranscriptStore(tempfile.mkdtemp()))

    def reset_after_pauses(self):
        """Experiment (not in the app): once the Captioner has flushed after a pause and gone idle, reset
        the stream, so the next words start on a fresh stream primed with WARMUP_S of silence, as a new
        stream is. The Captioner's own 20,000-character reset does the same bookkeeping."""
        cap, orig = self.cap, self.cap.flush
        self.resets = 0

        def flush():
            was = cap.flushed
            orig()
            if not was and not cap.partial.strip():
                cap.rec.reset(cap.stream)
                cap.done, cap.result, cap.primed = 0, None, False
                cap._decode(np.zeros(0, np.float32))  # prime now, in the pause, not when speech resumes
                self.resets += 1
        cap.flush = flush

    def _update(self, _text):
        c = self.clock
        self.events.append((c['block_end'] + time.perf_counter() - c['t0'], len(self.cap.raw), self.cap.partial))

    def feed(self, x, block_end):
        self.clock['block_end'], self.clock['t0'] = block_end, time.perf_counter()
        self.cap.feed(x.tobytes())

    def finish(self):
        pass

    def histories(self, starts):
        return by_display_time(self.cap.raw, self.events, starts)


def by_display_time(lines, events, starts):
    """Per clip: [(time shown, words so far)], as pipeline.py splits the session by display time.
    events are (time, finished lines so far, current partial line)."""
    line_words = [words(line) for line in lines]
    before = np.cumsum([0] + [len(w) for w in line_words])
    flat = [w for ws in line_words for w in ws]
    shown = lambda e: flat[:before[e[1]]] + words(e[2])
    out, k = [], 0
    ev = events
    for c, s0 in enumerate(starts):
        s1 = starts[c + 1] if c + 1 < len(starts) else float('inf')
        while k < len(ev) and ev[k][0] < s0:
            k += 1
        base = len(shown(ev[k - 1])) if k else 0
        hist, j = [], k
        while j < len(ev) and ev[j][0] < s1:
            hist.append((ev[j][0], shown(ev[j])[base:]))
            j += 1
        out.append(hist)
    return out


class April:
    """LiveCaptions' engine: april-asr, fed each block synchronously (LiveCaptions feeds it from a
    realtime thread; at well under real time that gives the same results)."""

    def __init__(self, path):
        import april_asr as april
        self.kinds = (april.Result.PARTIAL_RECOGNITION, april.Result.FINAL_RECOGNITION)
        self.model = april.Model(path)
        self.lines, self.partial, self.events, self.clock = [], '', [], {}
        self.session = april.Session(self.model, self._result)

    def _result(self, kind, tokens):
        if kind not in self.kinds:
            return  # SILENCE (and ERROR_CANT_KEEP_UP, which only the asynchronous mode reports)
        text = ''.join(t.token for t in tokens).strip()
        if kind == self.kinds[1]:
            if text:
                self.lines.append(text)
            text = ''
        if kind == self.kinds[1] or text != self.partial:
            self.partial = text
            self.events.append((self.clock['block_end'] + time.perf_counter() - self.clock['t0'], len(self.lines), text))

    def feed(self, x, block_end):
        self.clock['block_end'], self.clock['t0'] = block_end, time.perf_counter()
        self.session.feed_pcm16(x.tobytes())

    def finish(self):
        self.session.flush()

    def histories(self, starts):
        return by_display_time(self.lines, self.events, starts)


class Vosk:
    """Vosk (Kaldi): AcceptWaveform per block; a finished result becomes a line, otherwise the partial."""

    def __init__(self, folder):
        import vosk
        vosk.SetLogLevel(-1)
        self.rec = vosk.KaldiRecognizer(vosk.Model(folder), RATE)
        self.lines, self.partial, self.events, self.clock = [], '', [], {}

    def _show(self, final, text):
        if final:
            if text:
                self.lines.append(text)
            text = ''
        if final or text != self.partial:
            self.partial = text
            self.events.append((self.clock['block_end'] + time.perf_counter() - self.clock['t0'], len(self.lines), text))

    def feed(self, x, block_end):
        self.clock['block_end'], self.clock['t0'] = block_end, time.perf_counter()
        if self.rec.AcceptWaveform(x.tobytes()):
            self._show(True, json.loads(self.rec.Result()).get('text', ''))
        else:
            self._show(False, json.loads(self.rec.PartialResult()).get('partial', ''))

    def finish(self):
        self._show(True, json.loads(self.rec.FinalResult()).get('text', ''))

    def histories(self, starts):
        return by_display_time(self.lines, self.events, starts)


class Hypotheses:
    """ufal/whisper_streaming's HypothesisBuffer (MIT): LocalAgreement-2 over timed words."""

    def __init__(self):
        self.committed_in_buffer, self.buffer, self.new = [], [], []
        self.last_time = 0.0

    def insert(self, new, offset):
        self.new = [(a + offset, b + offset, t) for a, b, t in new if a + offset > self.last_time - 0.1]
        if self.new and abs(self.new[0][0] - self.last_time) < 1 and self.committed_in_buffer:
            # drop 1-5 words at the start of the new hypothesis that repeat the end of the committed text
            cn, nn = len(self.committed_in_buffer), len(self.new)
            for i in range(1, min(cn, nn, 5) + 1):
                tail = ' '.join(self.new[j - 1][2] for j in range(1, i + 1))
                if ' '.join(self.committed_in_buffer[-j][2] for j in range(i, 0, -1)) == tail:
                    del self.new[:i]
                    break

    def flush(self):
        """Commit the longest common prefix of the last two hypotheses."""
        commit = []
        while self.new and self.buffer and self.new[0][2] == self.buffer[0][2]:
            commit.append(self.new[0])
            self.last_time = self.new[0][1]
            self.buffer.pop(0)
            self.new.pop(0)
        self.buffer, self.new = self.new, []
        self.committed_in_buffer.extend(commit)
        return commit

    def pop_committed(self, t):
        while self.committed_in_buffer and self.committed_in_buffer[0][1] <= t:
            self.committed_in_buffer.pop(0)


class Whisper:
    """faster-whisper with ufal/whisper_streaming's OnlineASRProcessor (LocalAgreement-2, buffer trimmed at
    completed segments once it passes 15 s), with its FasterWhisperASR settings (beam 5, word timestamps,
    condition on previous text) plus faster-whisper's VAD filter (whisper_online.py --vad), which keeps
    it from writing words into the pauses. Runs on its own clock: a pass starts when --min-chunk seconds
    of new audio are waiting and the previous pass has finished, and takes everything waiting by then."""
    TRIM_S = 15

    def __init__(self, folder, threads=4, min_chunk=1.0):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(folder, device='cpu', compute_type='int8', cpu_threads=threads)
        self.min_chunk = min_chunk
        self.audio = np.zeros(0, np.float32)
        self.offset = 0.0           # session time of self.audio[0]
        self.hyp = Hypotheses()
        self.committed = []         # (start, end, word) in session time
        self.waiting, self.busy = [], 0.0
        self.events = []            # (time shown, words committed so far, uncommitted tail)
        self.clock = {}

    def _prompt(self):
        """Up to 200 characters of committed text that is no longer in the audio buffer."""
        k = max(0, len(self.committed) - 1)
        while k > 0 and self.committed[k - 1][1] > self.offset:
            k -= 1
        out, n = [], 0
        for _, _, t in reversed(self.committed[:k]):
            if n >= 200:
                break
            out.append(t)
            n += len(t) + 1
        return ''.join(reversed(out))

    def _pass(self, now):
        t0 = time.perf_counter()
        segs, _ = self.model.transcribe(self.audio, language='en', initial_prompt=self._prompt(), beam_size=5,
                                        word_timestamps=True, condition_on_previous_text=True, vad_filter=True)
        segs = list(segs)
        timed = [(w.start, w.end, w.word) for s in segs if s.no_speech_prob <= 0.9 for w in (s.words or [])]
        self.hyp.insert(timed, self.offset)
        self.committed.extend(self.hyp.flush())
        if len(self.audio) / RATE > self.TRIM_S and self.committed:
            ends = [s.end for s in segs]
            t = self.committed[-1][1]
            if len(ends) > 1:
                e = ends[-2] + self.offset
                while len(ends) > 2 and e > t:
                    ends.pop()
                    e = ends[-2] + self.offset
                if e <= t:
                    self.hyp.pop_committed(e)
                    self.audio = self.audio[int((e - self.offset) * RATE):]
                    self.offset = e
        done = now + time.perf_counter() - t0
        self.events.append((done, len(self.committed), list(self.hyp.buffer)))
        return done

    def feed(self, x, arrival):
        self.waiting.append(x)
        if arrival >= self.busy and sum(len(w) for w in self.waiting) >= self.min_chunk * RATE:
            self.audio = np.concatenate([self.audio] + [w.astype(np.float32) / 32768 for w in self.waiting])
            self.waiting = []
            self.busy = self._pass(arrival)

    def finish(self):
        """At the end of the session: one last pass over what is left, then everything shown is final."""
        if self.waiting:
            self.audio = np.concatenate([self.audio] + [w.astype(np.float32) / 32768 for w in self.waiting])
            self.waiting = []
        t = self._pass(max(self.clock['block_end'], self.busy))
        self.committed.extend(self.hyp.buffer)
        self.hyp.buffer = []
        self.events.append((t, len(self.committed), []))

    def histories(self, starts):
        """Per clip: [(time shown, words so far)]. Words go to the clip their audio is in, by Whisper's
        timestamps (as Moonshine's lines go by their start), since Whisper can show a clip's words after
        the next clip has started."""
        import bisect
        clip = lambda st: max(0, bisect.bisect_right(starts, st + 1.0) - 1)
        done = [[] for _ in starts]
        tail = [[] for _ in starts]
        out = [[] for _ in starts]
        n = 0
        for t, upto, buf in self.events:
            touched = {c for c in range(len(starts)) if tail[c]}
            for c in touched:
                tail[c] = []
            for st, _, w in self.committed[n:upto]:
                done[clip(st)] += words(w)
                touched.add(clip(st))
            n = upto
            for st, _, w in buf:
                tail[clip(st)] += words(w)
                touched.add(clip(st))
            for c in sorted(touched):
                out[c].append((t, done[c] + tail[c]))
        return out


class Moonshine:
    """Moonshine Streaming through moonshine-voice's Stream (its own VAD, lines and update cadence)."""
    ARCH = {'small': 'SMALL_STREAMING', 'medium': 'MEDIUM_STREAMING', 'tiny': 'TINY_STREAMING'}

    def __init__(self, size, interval=0.5, options=None):
        os.environ.setdefault('MOONSHINE_VOICE_CACHE', str(ROOT / 'moonshine'))
        from moonshine_voice.download import get_model_for_language
        from moonshine_voice.moonshine_api import ModelArch
        from moonshine_voice.transcriber import Transcriber
        path, arch = get_model_for_language('en', getattr(ModelArch, self.ARCH[size]))
        self.t = Transcriber(path, arch, update_interval=interval, options=options or None)
        self.stream = self.t.create_stream(update_interval=interval)
        self.lines = {}     # line id -> (start time, text)
        self.events = []    # (time shown, {line id: text} of the lines that changed)
        orig = self.stream.update_transcription

        def update(flags=0):
            tr = orig(flags)
            changed = {}
            for line in tr.lines:
                old = self.lines.get(line.line_id)
                if old is None or old[1] != line.text:
                    self.lines[line.line_id] = (line.start_time, line.text)
                    changed[line.line_id] = line.text
            if changed:
                self.events.append((self.clock['block_end'] + time.perf_counter() - self.clock['t0'], changed))
            return tr
        self.stream.update_transcription = update
        self.clock = {}
        self.stream.start()

    def feed(self, x, block_end):
        self.clock['block_end'], self.clock['t0'] = block_end, time.perf_counter()
        self.stream.add_audio((x.astype(np.float32) / 32768).tolist(), RATE)

    def finish(self):
        self.stream.stop()  # transcribes what is left; stamped at the session's end

    def histories(self, starts):
        """Per clip: [(time shown, words so far)]. A line belongs to the clip it starts in (or
        starts just before: Moonshine's VAD looks back a little)."""
        import bisect
        clip_of = {lid: max(0, bisect.bisect_right(starts, st + 1.0) - 1) for lid, (st, _) in self.lines.items()}
        out = [[] for _ in starts]
        texts = [dict() for _ in starts]
        for t, changed in self.events:
            touched = set()
            for lid, text in changed.items():
                c = clip_of[lid]
                texts[c][lid] = text
                touched.add(c)
            for c in touched:
                out[c].append((t, [w for lid in sorted(texts[c]) for w in words(texts[c][lid])]))
        return out


def norm_wer(refs, hyps):
    import jiwer
    from whisper_normalizer.english import EnglishTextNormalizer
    norm = EnglishTextNormalizer()
    pairs = [(norm(r), norm(h)) for r, h in zip(refs, hyps)]
    pairs = [(r, h) for r, h in pairs if r.strip()]  # fillers only ("Hmm.") normalise to nothing
    out = jiwer.process_words([r for r, _ in pairs], [h for _, h in pairs])
    return {'wer': round(out.wer, 4), 'words': sum(len(r.split()) for r, _ in pairs), 'clips': len(pairs),
            'sub': out.substitutions, 'del': out.deletions, 'ins': out.insertions}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('engine')
    ap.add_argument('--data', required=True, help='a set folder with refs.jsonl (and word_times.json for delays)')
    ap.add_argument('--name')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--gap', type=float, nargs=2, default=(2.0, 2.1))
    ap.add_argument('--interval', type=float, default=0.5, help='Moonshine update_interval')
    ap.add_argument('--threads', type=int, help='sherpa-onnx threads (default 1, as the app) or faster-whisper threads (default 4)')
    ap.add_argument('--min-chunk', type=float, default=1.0, help='Whisper: seconds of new audio before a pass')
    ap.add_argument('--options', default='', help='Moonshine transcriber options, key=value,key=value')
    ap.add_argument('--limit', type=int)
    a = ap.parse_args()
    D = Path(a.data)
    refs = [json.loads(line) for line in open(D / 'refs.jsonl')][:a.limit]
    timing = json.loads((D / 'word_times.json').read_text()) if (D / 'word_times.json').exists() else {}
    kind, _, arg = a.engine.partition(':')
    name = a.name or (Path(arg).name if kind in ('app', 'appreset') else f'moonshine-{arg}' + ('' if a.interval == 0.5 else f'-i{a.interval:g}')
                      if kind == 'moonshine' else f'{kind}-{Path(arg).stem}')

    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    opts = dict(kv.split('=', 1) for kv in a.options.split(',') if kv)
    if kind in ('app', 'appreset'):
        eng = App(arg, a.threads or 1)
    elif kind == 'moonshine':
        eng = Moonshine(arg, a.interval, opts)
    elif kind == 'april':
        eng = April(arg)
    elif kind == 'vosk':
        eng = Vosk(arg)
    elif kind == 'whisper':
        eng = Whisper(arg, a.threads or 4, a.min_chunk)
    else:
        raise SystemExit(f'unknown engine {kind}')
    if kind == 'appreset':
        eng.reset_after_pauses()
    lead, starts, gaps, total = layout(D, refs, a.seed, a.gap)
    cpu0, wall0 = os.times(), time.perf_counter()
    off, busy = 0, 0.0
    for x in blocks(D, refs, lead, gaps):
        off += len(x)
        if isinstance(eng, Whisper):  # keeps its own clock (a pass takes all the audio waiting)
            eng.feed(x, off / RATE)
            continue
        # A live app handles this block when its audio has arrived and the previous block is done.
        start, t = max(off / RATE, busy), time.perf_counter()
        eng.feed(x, start)
        busy = start + time.perf_counter() - t
    eng.clock['block_end'], eng.clock['t0'] = max(off / RATE, busy), time.perf_counter()
    eng.finish()
    cpu1, wall = os.times(), time.perf_counter() - wall0
    cpu = (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
    audio_s = off / RATE
    threads = len(os.listdir('/proc/self/task'))

    from rapidfuzz.distance import Levenshtein
    first, settled, shown, stable, items = [], [], [], [], []
    for k, (row, hist) in enumerate(zip(refs, eng.histories(starts))):
        s0 = starts[k]
        ref, final = words(row['text']), (hist[-1][1] if hist else [])
        err, match = align(ref, final)
        ends = timing.get(row['audio'], {})
        # Which final words each update shows, wherever they sit: an engine that corrects an earlier
        # word shifts the words after it, which the position test counts as not shown yet.
        on = [{i for tag, i1, i2, _, _ in Levenshtein.opcodes(final, ws) if tag == 'equal' for i in range(i1, i2)}
              for _, ws in hist]
        word_lags, word_shown, word_settled = {}, {}, {}  # reference word index -> delay (first shown / on screen / settled)
        for ri, hi in match.items():
            if str(ri) not in ends:
                continue
            w, end = final[hi], s0 + ends[str(ri)]
            ok = [len(ws) > hi and ws[hi] == w for _, ws in hist]
            first.append(next(t for (t, _), o in zip(hist, ok) if o) - end)
            word_lags[ri] = round(first[-1], 3)
            last_bad = max((i for i, o in enumerate(ok) if not o), default=-1)
            settled.append(hist[last_bad + 1][0] - end)
            word_settled[ri] = round(settled[-1], 3)
            seen = [hi in o for o in on]
            shown.append(next(t for (t, _), o in zip(hist, seen) if o) - end)
            word_shown[ri] = round(shown[-1], 3)
            last_bad = max((i for i, o in enumerate(seen) if not o), default=-1)
            stable.append(hist[last_bad + 1][0] - end)
        items.append({'audio': row['audio'], 'ref': row['text'], 'hyp': ' '.join(final), 'raw_errors': err,
                      'lags': word_lags, 'shown': word_shown, 'settled': word_settled})

    pct = lambda v, q: round(float(np.percentile(v, q)), 3) if v else None
    out = {'name': name, 'engine': a.engine, 'data': str(D.relative_to(ROOT)) if D.is_relative_to(ROOT) else str(D),
           'seed': a.seed, 'gap_s': list(a.gap), 'interval': a.interval if kind == 'moonshine' else None, 'options': opts,
           'min_chunk': a.min_chunk if kind == 'whisper' else None, 'queued': True,
           **norm_wer([r['text'] for r in refs], [i['hyp'] for i in items]),
           'raw_wer': round(sum(i['raw_errors'] for i in items) / max(1, sum(len(words(r['text'])) for r in refs)), 4),
           'lag_first_p50': pct(first, 50), 'lag_first_p90': pct(first, 90), 'lag_first_mean': round(float(np.mean(first)), 3) if first else None,
           'lag_settled_p50': pct(settled, 50), 'lag_settled_p90': pct(settled, 90), 'timed_words': len(first),
           'lag_first_p99': pct(first, 99), 'lag_settled_p99': pct(settled, 99),
           'lag_settled_mean': round(float(np.mean(settled)), 3) if settled else None,
           'resets': getattr(eng, 'resets', None), 'clips_total': len(refs),
           'lag_shown_p50': pct(shown, 50), 'lag_shown_p90': pct(shown, 90), 'lag_shown_mean': round(float(np.mean(shown)), 3) if shown else None,
           'lag_stable_p50': pct(stable, 50), 'lag_stable_p90': pct(stable, 90),
           'audio_s': round(audio_s, 1), 'cpu_rtf': round(cpu / audio_s, 4), 'wall_rtf': round(wall / audio_s, 4),
           'threads': threads, 'peak_rss_mb': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
           'start_rss_mb': round(rss0 / 1024), 'cpus': sorted(os.sched_getaffinity(0))}
    print(json.dumps(out), flush=True)
    out['items'] = items
    res = ROOT / 'results' / 'stream'
    res.mkdir(parents=True, exist_ok=True)
    (res / f'{name}-{D.name}-s{a.seed}.json').write_text(json.dumps(out, indent=1))


if __name__ == '__main__':
    sys.exit(main())
