"""Train the SentencePiece models and encode data/clean/*.tsv into flat uint16 arrays.

Source: one 64k unigram model shared by the 33 languages. Target: a 16k English model.
While encoding, some sources are varied the way live recognition text varies: the final
punctuation missing (a sentence still being spoken, 10%), or lower case without any
punctuation (4%). 0.5% of pairs are English -> the same English, so English that reaches
the model passes through. The FLEURS dev set stays out (it is used for evaluation).
Teacher translations in data/kd/<lang>.tsv (distill.py) are added KD_REPEATS times over.
Usage: prepare.py spm | encode
"""
import glob, json, os, random, re, sys, unicodedata
import numpy as np

ROOT = os.getcwd()  # the work folder: data/ and runs/ live here
D = os.environ.get('NCMT_DATA', f'{ROOT}/data')
LANGS = sorted(os.path.basename(p)[:-4] for p in glob.glob(f'{D}/clean/*.tsv'))
END_PUNCT = re.compile(r'[\s.?!。？！।؟…:;,、，"\'”’»)\]」』]+$')
ALL_PUNCT = re.compile(r'[^\w\s]', re.UNICODE)
MAX_TOK = 200
ODD_BREAKS = re.compile('[\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]')


SKIP_CORPORA = {'JESC'}  # its English is all lower case


KD_REPEATS = int(os.environ.get('KD_REPEATS', 8))  # teacher translations are seen this many times as often as the rest


def rows(lang, folder='clean'):
    """(source, english) pairs of data/<folder>/<lang>.tsv; only \\n ends a line."""
    with open(f'{D}/{folder}/{lang}.tsv', newline='') as f:
        for line in f.read().split('\n'):
            parts = ODD_BREAKS.sub(' ', line).split('\t')
            if len(parts) == 3 and parts[2] not in SKIP_CORPORA:
                yield parts[0], parts[1]


def spm_train():
    import sentencepiece as spm
    os.makedirs(f'{D}/spm', exist_ok=True)
    rng = random.Random(1)
    src, tgt = open(f'{D}/spm/src.txt', 'w'), open(f'{D}/spm/tgt.txt', 'w')
    for lang in LANGS:
        lines = list(rows(lang))
        for x, _ in rng.sample(lines, min(len(lines), 150_000)):
            src.write(x + '\n')
        for _, e in rng.sample(lines, min(len(lines), 70_000)):
            tgt.write(e + '\n')
        print('sampled', lang, flush=True)
    src.close(), tgt.close()
    common = dict(model_type='unigram', pad_id=0, unk_id=1, bos_id=2, eos_id=3, byte_fallback=True,
                  shuffle_input_sentence=True, num_threads=16, train_extremely_large_corpus=False)
    spm.SentencePieceTrainer.train(input=f'{D}/spm/src.txt', model_prefix=f'{D}/spm/source', vocab_size=int(os.environ.get('SRC_VOCAB', 64000)),
                                   character_coverage=0.9995, input_sentence_size=4_000_000, **common)
    spm.SentencePieceTrainer.train(input=f'{D}/spm/tgt.txt', model_prefix=f'{D}/spm/target', vocab_size=int(os.environ.get('TGT_VOCAB', 16000)),
                                   character_coverage=0.9999, input_sentence_size=2_500_000, **common)


def vary(x, r):
    if r < 0.10:
        y = END_PUNCT.sub('', x)
        return y if y else x
    if r < 0.14:
        y = re.sub(r'\s+', ' ', ALL_PUNCT.sub(' ', x)).strip().lower()
        return y if y else x
    return x


def encode_lang(job):
    folder, lang = job if isinstance(job, tuple) else ('clean', job)
    name = lang if folder == 'clean' else f'kd-{lang}'
    if os.path.exists(f'{D}/bin/{name}.npz'):
        z = np.load(f'{D}/bin/{name}.npz')
        print(name, 'already encoded', flush=True)
        return name, len(z['src_len']), int(z['src_len'].sum()), int(z['tgt_len'].sum())
    import sentencepiece as spm
    s = spm.SentencePieceProcessor(model_file=f'{D}/spm/source.model')
    t = spm.SentencePieceProcessor(model_file=f'{D}/spm/target.model')
    rng = random.Random(sum(map(ord, lang)) + 7)
    parts = {'src': [], 'tgt': [], 'src_len': [], 'tgt_len': []}

    def flush(srcs, tgts):
        S, T = s.encode(srcs), t.encode(tgts)
        keep = [i for i in range(len(S)) if 0 < len(S[i]) < MAX_TOK and 0 < len(T[i]) < MAX_TOK]
        parts['src'].append(np.fromiter((tok for i in keep for tok in S[i] + [3]), dtype=np.uint16))
        parts['tgt'].append(np.fromiter((tok for i in keep for tok in T[i] + [3]), dtype=np.uint16))
        parts['src_len'].append(np.array([len(S[i]) + 1 for i in keep], dtype=np.uint16))
        parts['tgt_len'].append(np.array([len(T[i]) + 1 for i in keep], dtype=np.uint16))

    srcs, tgts = [], []
    pairs = list(rows(lang, folder))
    for _ in range(1 if folder == 'clean' else KD_REPEATS):  # each repeat gets its own variations
        for x, e in pairs:
            r = rng.random()
            if r < 0.005:
                x = e  # English passes through
            else:
                x = vary(x, rng.random())
            srcs.append(x), tgts.append(e)
            if len(srcs) == 200_000:
                flush(srcs, tgts)
                srcs, tgts = [], []
    if srcs:
        flush(srcs, tgts)
    arrays = {k: np.concatenate(v) for k, v in parts.items()}
    np.savez(f'{D}/bin/{name}.npz', **arrays)
    n, ntok = len(arrays['src_len']), int(arrays['tgt_len'].sum())
    print(name, n, 'pairs', ntok, 'target tokens', flush=True)
    return name, n, int(arrays['src_len'].sum()), ntok


def encode():
    from multiprocessing import Pool
    os.makedirs(f'{D}/bin', exist_ok=True)
    kd = sorted(os.path.basename(p)[:-4] for p in glob.glob(f'{D}/kd/*.tsv'))
    with Pool(8) as p:
        stats = p.map(encode_lang, LANGS + [('kd', l) for l in kd], chunksize=1)
    langs = [l for l, *_ in stats]
    parts = [np.load(f'{D}/bin/{l}.npz') for l in langs]
    for key in ('src', 'tgt', 'src_len', 'tgt_len'):
        np.concatenate([p[key] for p in parts]).tofile(f'{D}/bin/{key}.u16')
    lang_id = np.concatenate([np.full(len(p['src_len']), i, np.uint8) for i, p in enumerate(parts)])
    lang_id.tofile(f'{D}/bin/lang.u8')
    json.dump({'langs': langs, 'stats': stats}, open(f'{D}/bin/meta.json', 'w'), indent=1)
    print('total pairs', sum(s[1] for s in stats), 'target tokens', sum(s[3] for s in stats))


if __name__ == '__main__':
    {'spm': spm_train, 'encode': encode}[sys.argv[1]]()
