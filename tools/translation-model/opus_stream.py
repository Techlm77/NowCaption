"""Read OPUS moses zips (X.txt.zip with <corpus>.<pair>.<x> and .en members) over HTTP range
requests, so a sample of a huge corpus does not need the whole file on disk."""
import io, urllib.request, zipfile, collections

BLOCK = 8 << 20


class RangeFile(io.RawIOBase):
    def __init__(self, url, cache_blocks=8):
        self.url, self.pos = url, 0
        req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'curl/8'})
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers['Content-Length'])
        self.cache = collections.OrderedDict()
        self.cache_blocks = cache_blocks
        self.fetched = 0

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos

    def seek(self, off, whence=0):
        self.pos = {0: off, 1: self.pos + off, 2: self.size + off}[whence]
        return self.pos

    def _block(self, i):
        if i in self.cache:
            self.cache.move_to_end(i)
            return self.cache[i]
        start, end = i * BLOCK, min(self.size, (i + 1) * BLOCK) - 1
        for attempt in range(6):
            try:
                req = urllib.request.Request(self.url, headers={'Range': f'bytes={start}-{end}', 'User-Agent': 'curl/8'})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                if len(data) == end - start + 1:
                    break
            except Exception:
                if attempt == 5:
                    raise
        self.fetched += len(data)
        self.cache[i] = data
        if len(self.cache) > self.cache_blocks:
            self.cache.popitem(last=False)
        return data

    def readinto(self, b):
        if self.pos >= self.size:
            return 0
        i, off = divmod(self.pos, BLOCK)
        data = self._block(i)
        n = min(len(b), len(data) - off)
        b[:n] = data[off:off + n]
        self.pos += n
        return n


def pairs(url, lang):
    """Yield (x, en) lines of an OPUS moses zip, x being the non-English side."""
    f = RangeFile(url) if url.startswith('http') else open(url, 'rb')
    z = zipfile.ZipFile(io.BufferedReader(f, buffer_size=1 << 20) if isinstance(f, RangeFile) else f)
    names = [n for n in z.namelist() if not n.endswith(('README', 'LICENSE', '.xml', '.ids', '.scores'))]
    en = [n for n in names if n.endswith('.en')]
    other = [n for n in names if not n.endswith('.en')]
    assert len(en) == 1 and len(other) == 1, names
    with z.open(other[0]) as fx, z.open(en[0]) as fe:
        for x, e in zip(io.TextIOWrapper(fx, 'utf-8', errors='replace', newline='\n'), io.TextIOWrapper(fe, 'utf-8', errors='replace', newline='\n')):
            yield x.rstrip('\n'), e.rstrip('\n')


if __name__ == '__main__':
    import sys, itertools
    url, lang, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    for x, e in itertools.islice(pairs(url, lang), n):
        print(x[:100], '|||', e[:100])
