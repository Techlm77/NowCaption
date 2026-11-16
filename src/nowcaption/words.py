"""The user's word list: words the speech model mishears, and how to write them.

Kept apart from the speech engine: it only changes text. engine.Captioner applies it to the
model's words before punctuation (WordFixes.heard) and to the punctuated words (WordFixes.spelled);
the More options window edits the saved list (load_words, save_words).
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from pathlib import Path

LOG = logging.getLogger(__name__)

EDGE_MARKS = '.,?!;:"\'()[]¿¡…“”‘’«»-'  # what punctuation may put around a word


class WordFixes:
    """The user's word list: words the speech model mishears, and how to write them.

    Each fix is (heard, write). heard is what the model writes instead, such as "in video" for
    "Nvidia" (it was never taught some names); every time it says that, write is shown instead.
    Without heard, the fix only spells write the user's way (casing, such as "NVIDIA") wherever
    the model hears it right. The model writes lower-case words, so heard is matched without case,
    and only as whole words. Spelling is applied after punctuation, keeping its marks: "nvidia's."
    shows as "Nvidia's.".
    """

    def __init__(self, fixes: Iterable[tuple[str, str]] = ()) -> None:
        self.fixes = []
        swaps, spell = [], {}
        for heard, write in fixes:
            write = ' '.join(str(write).split())
            heard = ' '.join(str(heard or '').lower().split())
            if not write:
                continue
            self.fixes.append((heard, write))
            words = tuple(write.split())
            spell[tuple(w.lower() for w in words)] = words
            if heard and heard != write.lower():
                swaps.append((heard, write.lower()))
        swaps.sort(key=lambda s: -len(s[0]))  # "and video card" before "and video"
        self.swaps = [(self.phrase(heard), self.phrase(write), write) for heard, write in swaps]
        self.spell = sorted(spell.items(), key=lambda s: -len(s[0]))  # longest phrase first
        self.longest = max((len(k) for k in spell), default=0)

    def __bool__(self) -> bool:
        return bool(self.fixes)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, WordFixes) and self.fixes == other.fixes

    @staticmethod
    def phrase(text: str) -> re.Pattern[str]:
        """A pattern for these words as whole words, with any spaces between them."""
        return re.compile(r"(?<![\w'])" + r'\s+'.join(map(re.escape, text.split())) + r"(?![\w'])")

    def heard(self, raw: str) -> str:
        """The model's raw words with each misheard phrase replaced (still lower case). A phrase
        that is part of the right words already ("studio" in "visual studio" for a fix that writes
        "Visual Studio") is left alone."""
        for heard, right, write in self.swaps:
            if heard.search(raw):
                spans = [m.span() for m in right.finditer(raw)]
                # sub() calls the lambda before the loop moves on, so it sees this pass's spans and write
                raw = heard.sub(lambda m: m.group(0) if any(a <= m.start() and m.end() <= b for a, b in spans)  # noqa: B023
                                else write, raw)  # noqa: B023
        return raw

    def spelled(self, words: list[str]) -> list[str]:
        """Punctuated words (a list) with the user's spelling; the same number of words."""
        if not self.spell:
            return words
        out, i = list(words), 0
        cores = [self.core(w) for w in out]
        while i < len(out):
            for key, spelling in self.spell:
                n = len(key)
                if tuple(c[1].lower() for c in cores[i:i + n]) == key:
                    for j in range(n):
                        before, core, after = cores[i + j]
                        if core != spelling[j]:
                            out[i + j] = before + spelling[j] + after
                    i += n
                    break
            else:
                i += 1
        return out

    @staticmethod
    def core(word: str) -> tuple[str, str, str]:
        """(marks before, the word, marks and 's after) of a punctuated word."""
        start, end = 0, len(word)
        while start < end and word[start] in EDGE_MARKS:
            start += 1
        while end > start and word[end - 1] in EDGE_MARKS:
            end -= 1
        core = word[start:end]
        for s in ("'s", "’s"):
            if len(core) > 2 and core.lower().endswith(s):
                return word[:start], core[:-2], core[-2:] + word[end:]
        return word[:start], core, word[end:]


def words_file() -> Path:
    """The word list's file: $XDG_CONFIG_HOME/nowcaption/words.json (next to settings.ini)."""
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return Path(base) / 'nowcaption' / 'words.json'


def load_words(path: str | Path | None = None) -> list[tuple[str, str]]:
    """The saved word list as [(heard, write)]; empty if there is none or it cannot be read."""
    path = Path(path or words_file())
    try:
        data = json.loads(path.read_text())
        return [(str(f.get('heard', '')), str(f['write'])) for f in data.get('fixes', [])
                if isinstance(f, dict) and str(f.get('write', '')).strip()]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        LOG.warning('Could not read the word list %s: %s', path, e)
        return []


def save_words(fixes: Iterable[tuple[str, str]], path: str | Path | None = None) -> None:
    """Save the word list, replacing the file in one step so it is never half written."""
    path = Path(path or words_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'fixes': [{'heard': heard, 'write': write} for heard, write in fixes]}
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, path)
