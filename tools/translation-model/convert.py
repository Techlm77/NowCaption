"""Checkpoint -> CTranslate2 model directory (int8 by default), with the two SentencePiece
models copied in as source.spm and target.spm.
Usage: convert.py <checkpoint.pt> <out-dir> [quantization]"""
import os, shutil, sys
import ctranslate2
import numpy as np
import torch
from ctranslate2.specs import common_spec, transformer_spec

sys.path.insert(0, os.path.dirname(__file__))
from model import Config, Translator, sinusoids


def _np(t):
    return t.detach().float().cpu().numpy()


def _linear(spec, lin, weight=None):
    spec.weight = _np(lin.weight if weight is None else weight)
    if lin is not None and lin.bias is not None:
        spec.bias = _np(lin.bias)


def _norm(spec, ln):
    spec.gamma, spec.beta = _np(ln.weight), _np(ln.bias)


def _attention(spec, att):
    _norm(spec.layer_norm, att.norm)
    if att.cross:
        _linear(spec.linear[0], att.q)
        _linear(spec.linear[1], att.kv)
        _linear(spec.linear[2], att.out)
    else:
        _linear(spec.linear[0], att.qkv)
        _linear(spec.linear[1], att.out)


def _ffn(spec, ffn):
    _norm(spec.layer_norm, ffn.norm)
    _linear(spec.linear_0, ffn.w0)
    _linear(spec.linear_1, ffn.w1)


def build_spec(m: Translator, src_vocab, tgt_vocab):
    c = m.c
    spec = transformer_spec.TransformerSpec.from_config((c.enc_layers, c.dec_layers), c.heads, pre_norm=True,
                                                        activation=common_spec.Activation.RELU)
    pos = _np(sinusoids(c.max_pos, c.d))
    enc, dec = spec.encoder, spec.decoder
    enc.embeddings[0].weight = _np(m.src_emb.weight)
    enc.position_encodings.encodings = pos
    _norm(enc.layer_norm, m.enc_norm)
    for s, l in zip(enc.layer, m.enc):
        _attention(s.self_attention, l.att)
        _ffn(s.ffn, l.ffn)
    dec.embeddings.weight = _np(m.tgt_emb.weight)
    dec.position_encodings.encodings = pos
    _norm(dec.layer_norm, m.dec_norm)
    dec.projection.weight = _np(m.tgt_emb.weight)
    for s, l in zip(dec.layer, m.dec):
        _attention(s.self_attention, l.att)
        _attention(s.attention, l.cross)
        _ffn(s.ffn, l.ffn)
    spec.config.layer_norm_epsilon = c.eps
    spec.config.add_source_eos = True
    spec.config.decoder_start_token = '<s>'
    spec.register_source_vocabulary(src_vocab)
    spec.register_target_vocabulary(tgt_vocab)
    return spec


class _Converter(ctranslate2.converters.Converter):
    def __init__(self, spec):
        self.spec = spec

    def _load(self):
        return self.spec


def convert(model, src_vocab, tgt_vocab, out, quantization='int8'):
    _Converter(build_spec(model, src_vocab, tgt_vocab)).convert(out, quantization=quantization, force=True)


def load_checkpoint(path):
    ck = torch.load(path, map_location='cpu', weights_only=False)
    m = Translator(Config(**ck['config']))
    m.load_state_dict(ck['model'])
    return m.eval()


if __name__ == '__main__':
    import sentencepiece as spm
    ck, out = sys.argv[1], sys.argv[2]
    q = sys.argv[3] if len(sys.argv) > 3 else 'int8'
    d = os.path.join(os.environ.get('NCMT_DATA', 'data'), 'spm')
    vocab = lambda p: [sp.id_to_piece(i) for sp in [spm.SentencePieceProcessor(model_file=p)] for i in range(sp.get_piece_size())]
    convert(load_checkpoint(ck), vocab(f'{d}/source.model'), vocab(f'{d}/target.model'), out, q)
    shutil.copy(f'{d}/source.model', f'{out}/source.spm')
    shutil.copy(f'{d}/target.model', f'{out}/target.spm')
    print('converted to', out, sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out)) // 1_000_000, 'MB')
