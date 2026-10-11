#!/usr/bin/env python3
"""Download the 73-clip LibriSpeech dev-clean sample (hf-internal-testing/librispeech_asr_dummy)
into benchmark/data as 16 kHz WAVs plus refs.json. Needs: huggingface_hub pyarrow soundfile."""
import io, json
from pathlib import Path
import pyarrow.parquet as pq, soundfile as sf
from huggingface_hub import hf_hub_download

out = Path(__file__).resolve().parent / 'data'
out.mkdir(exist_ok=True)
p = hf_hub_download('hf-internal-testing/librispeech_asr_dummy', 'clean/validation-00000-of-00001.parquet',
                    repo_type='dataset', local_dir=out)
refs = []
for r in pq.read_table(p).to_pylist():
    a, sr = sf.read(io.BytesIO(r['audio']['bytes']), dtype='int16')
    sf.write(out / f"{r['id']}.wav", a, sr, subtype='PCM_16')
    refs.append({'audio': f"{r['id']}.wav", 'text': r['text'], 'seconds': len(a) / sr})
(out / 'refs.json').write_text(json.dumps(refs, indent=1))
print(len(refs), 'clips in', out)
