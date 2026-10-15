"""Random small model: CTranslate2 float32 scores must match PyTorch log-probs."""
import sys, tempfile, numpy as np, torch, ctranslate2
sys.path.insert(0, 'scripts')
from model import Config, Translator
from convert import convert
torch.manual_seed(0)
c = Config(src_vocab=300, tgt_vocab=200, d=64, heads=4, ffn=128, enc_layers=3, dec_layers=2, dropout=0.0)
m = Translator(c).eval()
for p in m.parameters():  # break the symmetric init so layer norms and biases matter
    p.data += 0.05 * torch.randn_like(p)
sv = ['<pad>', '<unk>', '<s>', '</s>'] + [f's{i}' for i in range(296)]
tv = ['<pad>', '<unk>', '<s>', '</s>'] + [f't{i}' for i in range(196)]
out = tempfile.mkdtemp()
convert(m, sv, tv, out, 'float32')
tr = ctranslate2.Translator(out, device='cpu')
src = [[5, 17, 42, 99, 7], [8, 9, 250]]
tgt = [[11, 12, 13, 14], [20, 21]]
res = tr.score_batch([[sv[i] for i in s] for s in src], [[tv[i] for i in t] for t in tgt])
for b in range(2):
    s = torch.tensor([src[b] + [3]]); ti = torch.tensor([[2] + tgt[b]]); lab = tgt[b] + [3]
    lp = torch.log_softmax(m(s, ti), -1)[0, torch.arange(len(lab)), lab].tolist()
    print('torch', np.round(lp, 4)); print('ct2  ', np.round(res[b].log_probs, 4))
g = tr.translate_batch([[sv[i] for i in s] for s in src], beam_size=1, max_decoding_length=12)
pt = m.greedy(torch.tensor([src[0] + [3]]), max_len=12)[0].tolist()
print('ct2 greedy', g[0].hypotheses[0]); print('pt  greedy', [tv[i] for i in pt if i not in (0, 3)])
