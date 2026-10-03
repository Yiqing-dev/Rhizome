# SPDX-License-Identifier: Apache-2.0
"""Text normalisation shared by alias matching, keys and the lexical fallbacks."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_PUNCT = re.compile(r"[\s\-_/·•,.;:()\[\]{}'\"`~!?，。；：（）【】、]+")
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_WORD = re.compile(r"[a-z0-9]+|[㐀-鿿豈-﫿]")


def norm(s: str) -> str:
    """Case/punctuation/width-insensitive form used for exact alias matching."""
    s = unicodedata.normalize("NFKC", s).casefold().strip()
    s = _PUNCT.sub(" ", s)
    return " ".join(s.split())


def lang_of(s: str) -> str:
    return "zh" if _CJK.search(s) else "en"


def tokens(s: str) -> list[str]:
    return _WORD.findall(norm(s))


def char_ngrams(s: str, n: int = 3) -> list[str]:
    s = f" {norm(s)} "
    return [s[i:i + n] for i in range(max(1, len(s) - n + 1))]


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
