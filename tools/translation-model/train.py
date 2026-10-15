"""Train the many-to-English model on data/bin (from prepare.py encode).
Usage: train.py <run-dir> [--steps N] [--resume]
Every EVAL_EVERY updates: chrF on FLEURS dev (all 33 languages, greedy) and a checkpoint;
the last 6 checkpoints are kept for averaging (average.py)."""
import argparse, glob, json, math, os, queue, sys, threading, time
import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(__file__))
from model import BOS, EOS, PAD, Config, Translator

ROOT = os.getcwd()  # the work folder: data/ and runs/ live here
D = os.environ.get('NCMT_DATA', f'{ROOT}/data')
MODEL = dict(d=768, heads=12, ffn=3072, enc_layers=12, dec_layers=2)
MAX_TOK, ACCUM = 10000, 3  # padded tokens per micro-batch side; micro-batches per update
PEAK_LR, WARMUP, MIN_LR_FRAC = 7e-4, 4000, 0.05
EVAL_EVERY, KEEP = 2500, 6


class Data:
    def __init__(self):
        self.src = np.fromfile(f'{D}/bin/src.u16', dtype=np.uint16)
        self.tgt = np.fromfile(f'{D}/bin/tgt.u16', dtype=np.uint16)
        self.src_len = np.fromfile(f'{D}/bin/src_len.u16', dtype=np.uint16).astype(np.int64)
        self.tgt_len = np.fromfile(f'{D}/bin/tgt_len.u16', dtype=np.uint16).astype(np.int64)
        self.src_off = np.concatenate([[0], np.cumsum(self.src_len)])
        self.tgt_off = np.concatenate([[0], np.cumsum(self.tgt_len)])
        self.n = len(self.src_len)

    def batches(self, seed):
        """Index arrays of length-sorted batches over one shuffled pass."""
        rng = np.random.default_rng(seed)
        perm = rng.permutation(self.n)
        out = []
        for i in range(0, self.n, 2_000_000):
            chunk = perm[i:i + 2_000_000]
            longest = np.maximum(self.src_len[chunk], self.tgt_len[chunk])
            chunk = chunk[np.argsort(longest, kind='stable')]
            longest = np.sort(longest, kind='stable')
            start = 0
            while start < len(chunk):
                width = longest[min(start + 1, len(chunk) - 1)]
                size = max(1, MAX_TOK // max(int(width), 1))
                end = min(len(chunk), start + size)
                while end > start + 1 and (end - start) * longest[end - 1] > MAX_TOK:
                    end -= 1
                out.append(chunk[start:end])
                start = end
        rng.shuffle(out)
        return out

    def tensors(self, idx):
        S = int(self.src_len[idx].max())
        T = int(self.tgt_len[idx].max()) + 1
        src = np.zeros((len(idx), S), np.int64)
        tgt = np.zeros((len(idx), T), np.int64)
        tgt[:, 0] = BOS
        for r, i in enumerate(idx):
            a, b = self.src_off[i], self.src_off[i + 1]
            src[r, :b - a] = self.src[a:b]
            a, b = self.tgt_off[i], self.tgt_off[i + 1]
            tgt[r, 1:1 + b - a] = self.tgt[a:b]
        return torch.from_numpy(src).pin_memory(), torch.from_numpy(tgt).pin_memory()


def loader(data, start_epoch, skip, q):
    epoch = start_epoch
    while True:
        for k, idx in enumerate(data.batches(seed=1000 + epoch)):
            if skip:
                skip -= 1
                continue
            q.put((epoch, data.tensors(idx)))
        epoch += 1


class Dev:
    def __init__(self):
        import sentencepiece as spm
        self.s = spm.SentencePieceProcessor(model_file=f'{D}/spm/source.model')
        self.t = spm.SentencePieceProcessor(model_file=f'{D}/spm/target.model')
        self.sets = json.load(open(f'{D}/eval/fleurs_dev.json'))

    @torch.no_grad()
    def run(self, model):
        import sacrebleu
        model.eval()
        scores = {}
        for lang, items in self.sets.items():
            ids = [self.s.encode(it['source'])[:255] + [EOS] for it in items]
            order = sorted(range(len(ids)), key=lambda i: len(ids[i]))
            hyps = [''] * len(ids)
            for i in range(0, len(order), 64):
                b = order[i:i + 64]
                L = max(len(ids[j]) for j in b)
                src = torch.zeros((len(b), L), dtype=torch.long)
                for r, j in enumerate(b):
                    src[r, :len(ids[j])] = torch.tensor(ids[j])
                with torch.autocast('cuda', dtype=torch.bfloat16):
                    out = model.greedy(src.cuda()).tolist()
                for r, j in enumerate(b):
                    toks = [t for t in out[r] if t not in (PAD, BOS)]
                    toks = toks[:toks.index(EOS)] if EOS in toks else toks
                    hyps[j] = self.t.decode(toks)
            scores[lang] = round(sacrebleu.corpus_chrf(hyps, [[it['english'] for it in items]]).score, 2)
        model.train()
        return sum(scores.values()) / len(scores), scores


def lr_at(step, total):
    if step < WARMUP:
        return PEAK_LR * step / WARMUP
    p = min(1.0, (step - WARMUP) / max(1, total - WARMUP))
    return PEAK_LR * (MIN_LR_FRAC + (1 - MIN_LR_FRAC) * 0.5 * (1 + math.cos(math.pi * p)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run')
    ap.add_argument('--steps', type=int, default=70000)
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--new-data', action='store_true', help='with --resume: the data changed, start a fresh pass')
    ap.add_argument('--eval-every', type=int, default=EVAL_EVERY)
    a = ap.parse_args()
    os.makedirs(a.run, exist_ok=True)
    torch.manual_seed(1)
    data = Data()
    import sentencepiece as spm
    vocab = lambda n: spm.SentencePieceProcessor(model_file=f'{D}/spm/{n}.model').get_piece_size()
    cfg = Config(**MODEL, src_vocab=vocab('source'), tgt_vocab=vocab('target'))
    model = Translator(cfg).cuda()
    opt = torch.optim.AdamW(model.parameters(), lr=PEAK_LR, betas=(0.9, 0.98), eps=1e-8, weight_decay=0.0, fused=True)
    step, epoch, done_batches, best = 0, 0, 0, -1.0
    if a.resume:
        ck = torch.load(f'{a.run}/last.pt', map_location='cpu', weights_only=False)
        model.load_state_dict(ck['model'])
        opt.load_state_dict(ck['opt'])
        step, epoch, done_batches, best = ck['step'], ck['epoch'], ck['batches'], ck['best']
        if a.new_data:
            epoch, done_batches = epoch + 100, 0  # a different shuffle over the new data
    log = open(f'{a.run}/train.log', 'a')

    def say(*x):
        line = time.strftime('%H:%M:%S ') + ' '.join(str(v) for v in x)
        print(line, flush=True)
        log.write(line + '\n')
        log.flush()

    say('pairs', data.n, 'params', sum(p.numel() for p in model.parameters()), 'config', cfg.dict(), 'steps', a.steps)
    dev = Dev()
    q = queue.Queue(maxsize=64)
    threading.Thread(target=loader, args=(data, epoch, done_batches, q), daemon=True).start()
    model.train()
    t0, tok, loss_sum, n_sum = time.time(), 0, 0.0, 0
    cur_epoch, batches_in_epoch = epoch, done_batches
    while step < a.steps:
        for g in opt.param_groups:
            g['lr'] = lr_at(step, a.steps)
        micro = [q.get() for _ in range(ACCUM)]
        ntok = sum(int((t[:, 1:] != PAD).sum()) for _, (_, t) in micro)
        for ep, (src, tgt) in micro:
            if ep != cur_epoch:
                cur_epoch, batches_in_epoch = ep, 0
            batches_in_epoch += 1
            src, tgt = src.cuda(non_blocking=True), tgt.cuda(non_blocking=True)
            with torch.autocast('cuda', dtype=torch.bfloat16):
                logits = model(src, tgt[:, :-1])
            loss = F.cross_entropy(logits.float().reshape(-1, cfg.tgt_vocab), tgt[:, 1:].reshape(-1),
                                   ignore_index=PAD, label_smoothing=0.1, reduction='sum')
            (loss / ntok).backward()
            loss_sum += float(loss)
        n_sum += ntok
        tok += ntok
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        opt.zero_grad(set_to_none=True)
        step += 1
        if step % 100 == 0:
            dt = time.time() - t0
            say(f'step {step} epoch {cur_epoch} loss {loss_sum / n_sum:.3f} lr {lr_at(step, a.steps):.2e} '
                f'{tok / dt / 1000:.1f}k tok/s')
            t0, tok, loss_sum, n_sum = time.time(), 0, 0.0, 0
        if step % a.eval_every == 0 or step == a.steps:
            mean, per = dev.run(model)
            say(f'DEV step {step} mean chrF {mean:.2f}', json.dumps(per))
            state = {'model': model.state_dict(), 'config': cfg.dict(), 'step': step, 'dev': mean}
            torch.save(state, f'{a.run}/ck{step:06d}.pt')
            for old in sorted(glob.glob(f'{a.run}/ck*.pt'))[:-KEEP]:
                os.remove(old)
            if mean > best:
                best = mean
                torch.save(state, f'{a.run}/best.pt')
            torch.save({**state, 'opt': opt.state_dict(), 'epoch': cur_epoch, 'batches': batches_in_epoch,
                        'best': best}, f'{a.run}/last.pt')
            t0, tok = time.time(), 0
    say('done, best dev', best)


if __name__ == '__main__':
    main()
