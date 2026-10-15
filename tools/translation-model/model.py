"""Many-to-English Transformer laid out like CTranslate2's TransformerSpec (pre-norm, fixed
sinusoidal positions, scaled embeddings, fused attention projections, tied output), so
convert.py can copy the weights one to one."""
import math
from dataclasses import dataclass, asdict

import torch
import torch.nn as nn
import torch.nn.functional as F

PAD, UNK, BOS, EOS = 0, 1, 2, 3


@dataclass
class Config:
    src_vocab: int = 64000
    tgt_vocab: int = 16000
    d: int = 512
    heads: int = 8
    ffn: int = 2048
    enc_layers: int = 12
    dec_layers: int = 2
    max_pos: int = 512
    dropout: float = 0.1
    eps: float = 1e-6

    def dict(self):
        return asdict(self)


def sinusoids(n, d):
    pos = torch.arange(n, dtype=torch.float32)[:, None]
    inv = torch.exp(-math.log(10000.0) * torch.arange(0, d // 2, dtype=torch.float32) / (d // 2 - 1))
    t = pos * inv[None]
    return torch.cat([torch.sin(t), torch.cos(t)], dim=1)


class Attention(nn.Module):
    def __init__(self, c, cross):
        super().__init__()
        self.h, self.cross = c.heads, cross
        self.norm = nn.LayerNorm(c.d, eps=c.eps)
        if cross:
            self.q = nn.Linear(c.d, c.d)
            self.kv = nn.Linear(c.d, 2 * c.d)
        else:
            self.qkv = nn.Linear(c.d, 3 * c.d)
        self.out = nn.Linear(c.d, c.d)
        self.drop = c.dropout

    def split(self, x):
        b, t, _ = x.shape
        return x.view(b, t, self.h, -1).transpose(1, 2)

    def forward(self, x, mem=None, mask=None, causal=False):
        h = self.norm(x)
        if self.cross:
            q = self.split(self.q(h))
            k, v = map(self.split, self.kv(mem).chunk(2, dim=-1))
        else:
            q, k, v = map(self.split, self.qkv(h).chunk(3, dim=-1))
        o = F.scaled_dot_product_attention(q, k, v, attn_mask=mask, is_causal=causal and mask is None,
                                           dropout_p=self.drop if self.training else 0.0)
        o = o.transpose(1, 2).reshape(x.shape)
        return x + F.dropout(self.out(o), self.drop, self.training)


class FFN(nn.Module):
    def __init__(self, c):
        super().__init__()
        self.norm = nn.LayerNorm(c.d, eps=c.eps)
        self.w0 = nn.Linear(c.d, c.ffn)
        self.w1 = nn.Linear(c.ffn, c.d)
        self.drop = c.dropout

    def forward(self, x):
        h = F.dropout(F.relu(self.w0(self.norm(x))), self.drop, self.training)
        return x + F.dropout(self.w1(h), self.drop, self.training)


class EncLayer(nn.Module):
    def __init__(self, c):
        super().__init__()
        self.att, self.ffn = Attention(c, False), FFN(c)

    def forward(self, x, mask):
        return self.ffn(self.att(x, mask=mask))


class DecLayer(nn.Module):
    def __init__(self, c):
        super().__init__()
        self.att, self.cross, self.ffn = Attention(c, False), Attention(c, True), FFN(c)

    def forward(self, y, mem, mem_mask):
        return self.ffn(self.cross(self.att(y, causal=True), mem=mem, mask=mem_mask))


class Translator(nn.Module):
    def __init__(self, c: Config):
        super().__init__()
        self.c = c
        self.src_emb = nn.Embedding(c.src_vocab, c.d, padding_idx=PAD)
        self.tgt_emb = nn.Embedding(c.tgt_vocab, c.d, padding_idx=PAD)
        self.register_buffer('pos', sinusoids(c.max_pos, c.d), persistent=False)
        self.enc = nn.ModuleList(EncLayer(c) for _ in range(c.enc_layers))
        self.dec = nn.ModuleList(DecLayer(c) for _ in range(c.dec_layers))
        self.enc_norm = nn.LayerNorm(c.d, eps=c.eps)
        self.dec_norm = nn.LayerNorm(c.d, eps=c.eps)
        self.scale = math.sqrt(c.d)
        for name, p in self.named_parameters():
            if p.dim() == 2 and 'emb' not in name:
                nn.init.xavier_uniform_(p)
            elif name.endswith('bias'):
                nn.init.zeros_(p)
        nn.init.normal_(self.src_emb.weight, 0, c.d ** -0.5)
        nn.init.normal_(self.tgt_emb.weight, 0, c.d ** -0.5)
        with torch.no_grad():
            self.src_emb.weight[PAD].zero_()
            self.tgt_emb.weight[PAD].zero_()

    def encode(self, src):
        mask = (src != PAD)[:, None, None, :]  # True = attend
        x = self.src_emb(src) * self.scale + self.pos[:src.shape[1]]
        x = F.dropout(x, self.c.dropout, self.training)
        for layer in self.enc:
            x = layer(x, mask)
        return self.enc_norm(x), mask

    def decode(self, tgt_in, mem, mem_mask):
        y = self.tgt_emb(tgt_in) * self.scale + self.pos[:tgt_in.shape[1]]
        y = F.dropout(y, self.c.dropout, self.training)
        for layer in self.dec:
            y = layer(y, mem, mem_mask)
        return F.linear(self.dec_norm(y), self.tgt_emb.weight)

    def forward(self, src, tgt_in):
        mem, mask = self.encode(src)
        return self.decode(tgt_in, mem, mask)

    @torch.no_grad()
    def greedy(self, src, max_len=256):
        mem, mask = self.encode(src)
        ys = torch.full((src.shape[0], 1), BOS, dtype=torch.long, device=src.device)
        done = torch.zeros(src.shape[0], dtype=torch.bool, device=src.device)
        limit = min(max_len, int(src.shape[1] * 3 + 10), self.c.max_pos - 1)
        for _ in range(limit):
            nxt = self.decode(ys, mem, mask)[:, -1].argmax(-1)
            nxt = torch.where(done, torch.full_like(nxt, PAD), nxt)
            ys = torch.cat([ys, nxt[:, None]], dim=1)
            done |= nxt == EOS
            if done.all():
                break
        return ys[:, 1:]
