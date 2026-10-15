"""Teacher translations on a CUDA GPU: MADLAD-400 3B translates data/kd_src/<lang>.txt
(one source sentence per line) -> data/kd/<lang>.tsv (source \t english \t KD).
Sorted by length for full batches; resumable per chunk.
Usage: distill_gpu.py <madlad-ct2-dir> <lang>...
"""
import os, sys, time
import ctranslate2
import sentencepiece as spm

d, langs = sys.argv[1], sys.argv[2:]
tr = ctranslate2.Translator(d, device='cuda', compute_type='int8_float16')
sp = spm.SentencePieceProcessor(model_file=f'{d}/spiece.model')
CHUNK = 4096

for lang in langs:
    out = f'data/kd/{lang}.tsv'
    if os.path.exists(out):
        continue
    src = [s for s in open(f'data/kd_src/{lang}.txt').read().split('\n') if s]
    part = out + '.part'
    done = sum(1 for _ in open(part)) // CHUNK * CHUNK if os.path.exists(part) else 0
    if done:  # keep whole chunks only
        lines = open(part).read().split('\n')[:done]
        open(part, 'w').write('\n'.join(lines) + '\n')
    t_lang = time.time()
    with open(part, 'a') as f:
        for i in range(done, len(src), CHUNK):
            chunk = src[i:i + CHUNK]
            toks = [sp.encode('<2en> ' + s, out_type=str)[:250] + ['</s>'] for s in chunk]
            order = sorted(range(len(toks)), key=lambda j: len(toks[j]))
            t0 = time.time()
            res = tr.translate_batch([toks[j] for j in order], beam_size=1, max_decoding_length=256,
                                     max_batch_size=6144, batch_type='tokens')
            hyps = [''] * len(chunk)
            for j, r in zip(order, res):
                hyps[j] = sp.decode(r.hypotheses[0]).strip().replace('\t', ' ').replace('\n', ' ')
            f.write(''.join(f'{s}\t{h}\tKD\n' for s, h in zip(chunk, hyps)))
            f.flush()
            print(f'{lang} {i + len(chunk)}/{len(src)} {len(chunk) / (time.time() - t0):.0f} sent/s', flush=True)
    os.rename(part, out)
    print(lang, 'done in', round(time.time() - t_lang), 's', flush=True)
