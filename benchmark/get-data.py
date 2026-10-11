#!/usr/bin/env python3
"""Prepare benchmark audio as 16 kHz WAVs plus refs.json.

  python get-data.py                      # data/dummy: 73 dev-clean clips, all speaker 1272
  python get-data.py --set test-clean     # data/test-clean: N clips from each of 40 speakers
  python get-data.py --set test-other     # data/test-other: N clips from each of 33 speakers (harder audio)

Needs: huggingface_hub pyarrow soundfile numpy. The test sets download about 350 MB each.
"""
import argparse, io, json, random
from pathlib import Path
import pyarrow.parquet as pq, soundfile as sf
from huggingface_hub import hf_hub_download

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--set', default='dummy', choices=['dummy', 'test-clean', 'test-other'])
ap.add_argument('--per-speaker', type=int, default=4)
ap.add_argument('--parquet', help='use an already downloaded parquet file')
a = ap.parse_args()

out = Path(__file__).resolve().parent / 'data' / a.set
out.mkdir(parents=True, exist_ok=True)
if a.parquet:
    p = a.parquet
elif a.set == 'dummy':
    p = hf_hub_download('hf-internal-testing/librispeech_asr_dummy', 'clean/validation-00000-of-00001.parquet',
                        repo_type='dataset')
else:
    p = hf_hub_download('openslr/librispeech_asr', f'{a.set.split("-")[1]}/test/0000.parquet',
                        repo_type='dataset', revision='refs/convert/parquet')
rows = pq.read_table(p, columns=['audio', 'text', 'speaker_id', 'id']).to_pylist()
if a.set != 'dummy':  # a fixed random choice of 3-15 s utterances, the same number per speaker
    rng, by_speaker = random.Random(0), {}
    for r in sorted(rows, key=lambda r: r['id']):
        by_speaker.setdefault(r['speaker_id'], []).append(r)
    rows = []
    for spk in sorted(by_speaker):
        ok = [r for r in by_speaker[spk] if 3 <= sf.info(io.BytesIO(r['audio']['bytes'])).duration <= 15]
        rows += rng.sample(ok, min(a.per_speaker, len(ok)))
refs = []
for r in rows:
    x, sr = sf.read(io.BytesIO(r['audio']['bytes']), dtype='int16')
    assert sr == 16000
    sf.write(out / f"{r['id']}.wav", x, sr, subtype='PCM_16')
    refs.append({'audio': f"{r['id']}.wav", 'text': r['text'], 'seconds': len(x) / sr, 'speaker': r['speaker_id']})
(out / 'refs.json').write_text(json.dumps(refs, indent=1))
print(f"{len(refs)} clips, {len({r['speaker'] for r in refs})} speakers, "
      f"{sum(r['seconds'] for r in refs) / 60:.1f} min in {out}")
