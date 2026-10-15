"""Average the weights of several checkpoints (usually a little better than any one of them).
Usage: average.py <out.pt> <ck1.pt> <ck2.pt> ..."""
import sys
import torch

out, paths = sys.argv[1], sys.argv[2:]
avg, first = None, None
for p in paths:
    ck = torch.load(p, map_location='cpu', weights_only=False)
    if avg is None:
        first, avg = ck, {k: v.double().clone() for k, v in ck['model'].items()}
    else:
        for k, v in ck['model'].items():
            avg[k] += v.double()
state = {k: (v / len(paths)).to(first['model'][k].dtype) for k, v in avg.items()}
torch.save({'model': state, 'config': first['config'], 'step': first['step'], 'averaged': paths}, out)
print('averaged', len(paths), 'checkpoints into', out)
