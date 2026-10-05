# SPDX-License-Identifier: Apache-2.0
"""Text normalisation shared by alias matching, keys and the lexical fallbacks."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_PUNCT = re.compile(r"[\s\-_/·•,.;:()\[\]{}'\"`~!?，。；：（）【】、]+")
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_WORD = re.compile(r"[a-z0-9]+|[㐀-鿿豈-﫿]")


# Durable keys (free_key: "topic:<norm>", "idea:<sha of norm>") and entity_alias.norm are derived
# from norm(). Changing it orphans every decision, card and access record that names a key, so:
# bump NORM_VERSION, ship a data migration that recomputes alias norms and rewrites the keys in
# human_decision payloads, review_card.entity_key, access_log and retro_tag outputs, and update
# the golden test (tests/test_rxf.py::test_norm_and_keys_are_pinned).
NORM_VERSION = 1


def norm(s: str) -> str:
    """Case/punctuation/width-insensitive form used for exact alias matching."""
    s = unicodedata.normalize("NFKC", s).casefold().strip()
    s = _PUNCT.sub(" ", s)
    return " ".join(s.split())


def lang_of(s: str) -> str:
    return "zh" if _CJK.search(s) else "en"


def tokens(s: str) -> list[str]:
    return _WORD.findall(norm(s))


_RUN = re.compile(r"[㐀-鿿豈-﫿]+|[a-z0-9]+")


def lex_tokens(s: str) -> list[str]:
    """Words for lexical comparison: Latin words, and Chinese as overlapping character pairs (a
    single character says almost nothing: 的, 基, 因 ...)."""
    out: list[str] = []
    for run in _RUN.findall(norm(s)):
        if _CJK.match(run):
            out += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
        else:
            out.append(run)
    return out


def search_units(s: str, max_cjk: int = 24) -> tuple[list[str], list[str]]:
    """(full-text terms, substring terms) for a query. The FTS index is trigram-based: Chinese runs
    of 3+ characters become overlapping 3-character windows; 2-character runs can only be found
    by substring match; single characters are not searched (the vector step covers them)."""
    fts: list[str] = []
    like: list[str] = []
    cjk: list[str] = []
    for run in _RUN.findall(norm(s)):
        if _CJK.match(run):
            if len(run) >= 3:
                cjk += [run[i:i + 3] for i in range(len(run) - 2)]
            elif len(run) == 2:
                like.append(run)
        else:
            fts.append(run)
    return fts + list(dict.fromkeys(cjk))[:max_cjk], list(dict.fromkeys(like))


def char_ngrams(s: str, n: int = 3) -> list[str]:
    s = f" {norm(s)} "
    return [s[i:i + n] for i in range(max(1, len(s) - n + 1))]


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
