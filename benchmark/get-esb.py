"""Download the ESB English test sets and keep a fixed random sample of each as WAV + refs.

    python get-esb.py [--per-set 400] [--fresh]     (needs huggingface_hub pyarrow soundfile soxr numpy)

Writes data/esb/<set>/ (or data/esb-fresh/<set>/ with --fresh): 16 kHz WAVs and refs.jsonl. Then
`python word_times.py --data data/esb/<set>` adds the word end times that stream.py measures delay against.
The sample is fixed by a seed per set, so everyone gets the same 400 clips; NowCaption 1.2.0 was chosen with
data/esb and confirmed on data/esb-fresh.

Sets are chosen to look like NowCaption's real use rather than audiobooks: meetings (AMI),
phone and conference calls with accents (Earnings-22), podcasts and YouTube (GigaSpeech),
accented speeches (VoxPopuli), cheap microphones and accents (Common Voice), noisy speech
(URGENT 2024), plus LibriSpeech for comparison with benchmark/README.md.
"""
import argparse
import io
import json
import random
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
import soxr
from huggingface_hub import HfApi, hf_hub_download

REPO = 'hf-audio/esb-datasets-test-only-sorted'
SETS = {  # name: (folder, file prefix)
    'ami': ('ami', 'test-'),
    'earnings22': ('earnings22', 'test-'),
    'gigaspeech': ('gigaspeech', 'test-'),
    'voxpopuli': ('voxpopuli', 'test-'),
    'common_voice': ('common_voice', 'test-'),
    'urgent2024': ('urgent2024', 'test-'),
    'librispeech-clean': ('librispeech', 'test.clean-'),
    'librispeech-other': ('librispeech', 'test.other-'),
}
ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--per-set', type=int, default=400)
    ap.add_argument('--sets', nargs='*', default=list(SETS))
    ap.add_argument('--fresh', action='store_true',
                    help='a second sample with none of the first one\'s clips, in data/esb-fresh (to confirm a model picked by its score)')
    args = ap.parse_args()
    files = [s.rfilename for s in HfApi().dataset_info(REPO).siblings]
    for name in args.sets:
        folder, prefix = SETS[name]
        out = ROOT / 'data' / ('esb-fresh' if args.fresh else 'esb') / name
        if (out / 'refs.jsonl').exists():
            print(name, 'already prepared')
            continue
        shards = sorted(f for f in files if f.startswith(f'{folder}/{prefix}') and f.endswith('.parquet'))
        paths = [hf_hub_download(REPO, f, repo_type='dataset', cache_dir=str(ROOT / 'data' / 'hf')) for f in shards]
        index = []  # (shard, row) of every clip with a transcript
        for i, p in enumerate(paths):
            meta = pq.read_table(p, columns=['text'])
            index += [(i, r) for r, t in enumerate(meta.column('text').to_pylist()) if t and t.strip()]
        rng = random.Random(f'nowcaption-eval-{name}')
        chosen = sorted(rng.sample(index, min(args.per_set, len(index))))
        if args.fresh:  # (the first sample is always 400 clips)
            used = set(random.Random(f'nowcaption-eval-{name}').sample(index, min(400, len(index))))
            rest = [x for x in index if x not in used]
            chosen = sorted(random.Random(f'nowcaption-eval-fresh-{name}').sample(rest, min(args.per_set, len(rest))))
        out.mkdir(parents=True, exist_ok=True)
        refs, seconds = [], 0.0
        for i, p in enumerate(paths):
            rows = [r for s, r in chosen if s == i]
            if not rows:
                continue
            table = pq.read_table(p).take(rows).to_pylist()
            for row in table:
                audio, rate = sf.read(io.BytesIO(row['audio']['bytes']), dtype='float32', always_2d=True)
                audio = audio.mean(axis=1)
                if rate != 16000:  # Earnings-22 has some 22.05 kHz calls
                    audio = soxr.resample(audio, rate, 16000, quality='VHQ')
                clip = f'{len(refs):04d}.wav'
                sf.write(out / clip, np.clip(audio, -1, 1), 16000, subtype='PCM_16')
                refs.append({'audio': clip, 'id': row['id'], 'text': row['text'], 'seconds': len(audio) / 16000})
                seconds += len(audio) / 16000
        with open(out / 'refs.jsonl', 'w') as f:
            for r in refs:
                f.write(json.dumps(r) + '\n')
        print(f'{name}: {len(refs)} clips, {seconds / 60:.1f} min of {len(index)}')


if __name__ == '__main__':
    main()
