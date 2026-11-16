"""The recognizer's text from its tokens, without the copies of a word it repeats on one frame.

sherpa-onnx 1.13.8's greedy search lets NowCaption's model emit up to 10 tokens on each 80 ms
encoder frame. Once a word is in its context three or so times in a row, the next real copy can
make it emit that word again and again on one frame: "yes" said six times came out 47 times, and
in real recordings "opportunities", said once, came out four times, and "plus", said twice, five.
No one can say a word again in no time at all, so a word identical to the word before it that
starts on the frame where that word ended is dropped. The cost is a genuine repeat so fast that
the model put both copies on one frame: in 475 recordings with repeated words, one "is is" lost
its second "is".

Depends on nothing else in NowCaption.
"""
from __future__ import annotations

import numpy as np

# Two tokens are on the same frame when their times are closer than this. A frame's time is the
# same number each time, and frames are 80 ms apart for NowCaption's model; no model's are closer
# than the 10 ms step of the audio features.
SAME_FRAME_S = 0.005


def join_tokens(tokens: list[str], times: list[float]) -> str:
    """The recognizer's text: its tokens joined, without each word that copies the word before it
    and starts on the frame where that word ended. times are the tokens' frame times in seconds
    (sherpa-onnx's result.timestamps); without one per token, the tokens are only joined.

    As tokens arrive, the text only grows, as the Captioner needs (it keeps character offsets
    into it): greedy search never changes a token once it is emitted, a word is judged only
    against the word before it, and the last word is held back while it may still grow into a
    copy (" p" after " plus", on the frame where " plus" ended).
    """
    n = len(tokens)
    if len(times) != n:
        return ''.join(tokens).strip()
    # Only a word that starts on the frame of the token before it can be a copy. This runs after
    # every decode over the whole stream's tokens, and NumPy finds those frames in half the time
    # a Python loop takes.
    # sherpa-onnx gives SentencePiece's word marker as a space, so a word's first token starts
    # with one.
    same = np.flatnonzero(np.abs(np.diff(times)) < SAME_FRAME_S) + 1
    starts = [i for i in same.tolist() if tokens[i][:1] == ' ']
    copies = []  # (first token, end) of each word dropped
    for i in starts:
        first = i - 1  # the word before it starts at the first token or at a space
        while first and tokens[first][:1] != ' ':
            first -= 1
        end = i + 1
        while end < n and tokens[end][:1] != ' ':
            end += 1
        before, word = ''.join(tokens[first:i]), ''.join(tokens[i:end])
        if word == before or (end == n and before.startswith(word)):
            copies.append((i, end))
    if not copies:
        return ''.join(tokens).strip()
    kept, at = [], 0
    for first, end in copies:
        kept += tokens[at:first]
        at = end
    kept += tokens[at:]
    return ''.join(kept).strip()
