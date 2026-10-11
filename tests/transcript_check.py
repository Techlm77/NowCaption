"""Check the complete, ordered transcript emitted by the capture smoke tests."""
import argparse
import json
import re
from pathlib import Path

ABBR = {'mr': 'mister', 'mrs': 'missus', 'dr': 'doctor', 'st': 'saint'}


def words(text):
    return [ABBR.get(w, w) for w in re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text.lower())]


def transcript_words(text):
    return words(re.sub(r'^\[\d\d:\d\d:\d\d\]\s*', '', text, flags=re.MULTILINE))


def wer(ref, hyp):
    if not ref:
        raise ValueError('The reference transcript is empty')
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        new = [i]
        for j, h in enumerate(hyp, 1):
            new.append(min(new[-1] + 1, row[j] + 1, row[j - 1] + (r != h)))
        row = new
    return row[-1] / len(ref)


def check(output, references, max_wer=0.15):
    records = []
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue  # READY and native logging are not transcript words
        if isinstance(record, dict):
            records.append(record)
    failures = [r for r in records if r.get('kind') == 'error']
    if failures:
        raise ValueError(f'Capture or recognition failed: {failures[0].get("message", failures[0])}')
    transcripts = [r for r in records if r.get('kind') == 'transcript']
    if len(transcripts) != 1 or not isinstance(transcripts[0].get('text'), str):
        raise ValueError('Expected exactly one complete transcript record')
    ref = [w for r in references for w in words(r['text'])]
    score = wer(ref, transcript_words(transcripts[0]['text']))
    return score <= max_wer, score


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('references')
    parser.add_argument('--max-wer', type=float, default=0.15)
    args = parser.parse_args()
    try:
        ok, score = check(Path(args.output).read_text(), json.loads(Path(args.references).read_text()),
                          args.max_wer)
    except (ValueError, OSError, KeyError) as e:
        print(f'FAIL: {e}')
        return 1
    print(f'Word error rate: {score:.1%} (maximum {args.max_wer:.1%})')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
