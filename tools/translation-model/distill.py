"""Teacher translations for distillation: MADLAD-400 3B (Apache-2.0) translates a sample of
the training sources into English -> data/kd/<lang>.tsv (source \t english \t KD).
Resumable: a language that already has its file is skipped; a part file is continued.
Usage: distill.py <madlad-ct2-dir> <per-lang> <device cpu|cuda> <threads> <lang>...
"""
import os, random, sys, time
import ctranslate2
import sentencepiece as spm

ROOT = os.getcwd()
d, per_lang, device, threads, langs = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5:]
tr = ctranslate2.Translator(d, device=device, intra_threads=threads, inter_threads=1,
                            compute_type='int8' if device == 'cpu' else 'int8_float16')
sp = spm.SentencePieceProcessor(model_file=f'{d}/spiece.model')
os.makedirs(f'{ROOT}/data/kd', exist_ok=True)
BATCH = 32 if device == 'cpu' else 96

for lang in langs:
    out = f'{ROOT}/data/kd/{lang}.tsv'
    if os.path.exists(out):
        continue
    rows = []
    with open(f'{ROOT}/data/clean/{lang}.tsv', newline='') as f:
        for line in f.read().split('\n'):
            p = line.split('\t')
            if len(p) == 3 and p[2] != 'JESC' and 3 <= len(p[0]) <= 300:
                rows.append(p[0].replace(' ', ' ').replace(' ', ' ').replace('\r', ' '))
    src = random.Random(sum(map(ord, lang)) + 99).sample(rows, min(per_lang, len(rows)))
    part = out + '.part'
    done = sum(1 for _ in open(part)) if os.path.exists(part) else 0
    src = src[done:]
    t0 = time.time()
    with open(part, 'a') as f:
        for i in range(0, len(src), BATCH * 8):
            chunk = src[i:i + BATCH * 8]
            toks = [sp.encode('<2en> ' + s, out_type=str)[:250] + ['</s>'] for s in chunk]
            res = tr.translate_batch(toks, beam_size=1, max_decoding_length=256, max_batch_size=BATCH,
                                     batch_type='examples')
            for s, r in zip(chunk, res):
                en = sp.decode(r.hypotheses[0]).strip().replace('\t', ' ').replace('\n', ' ')
                f.write(f'{s}\t{en}\tKD\n')
            f.flush()
            n = done + i + len(chunk)
            print(f'{lang} {n}/{done + len(src)} {len(chunk) / max(time.time() - t0, 1e-6):.1f} sent/s', flush=True)
            t0 = time.time()
    os.rename(part, out)
    print(lang, 'done', flush=True)
