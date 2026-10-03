# SPDX-License-Identifier: Apache-2.0
"""Dependency-free fallbacks so Rhizome works before any model is downloaded.

* HashingEmbedder: signed feature hashing of words + character trigrams (CJK-friendly).
  Deterministic everywhere, so remote snapshots can embed queries without any model.
* LexicalReranker: max(word Jaccard, trigram Dice) blended with hashing cosine.
These are weaker than bge-m3 / bge-reranker; thresholds must be calibrated per model (G3).
"""

from __future__ import annotations

import hashlib

import numpy as np

from ..text import char_ngrams, tokens


def _h(feature: str) -> int:
    return int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "little")


class HashingEmbedder:
    name = "hashing-v1"

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _one(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        feats = [("w:" + t, 1.0) for t in tokens(text)] + [("c:" + g, 0.5) for g in char_ngrams(text)]
        for f, w in feats:
            h = _h(f)
            v[h % self.dim] += w if (h >> 63) & 1 else -w
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._one(t) for t in texts])


class LexicalReranker:
    name = "lexical-v1"

    def __init__(self) -> None:
        self._emb = HashingEmbedder(512)

    @staticmethod
    def _sim(a: str, b: str) -> float:
        ta, tb = set(tokens(a)), set(tokens(b))
        jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
        ga, gb = set(char_ngrams(a)), set(char_ngrams(b))
        dice = 2 * len(ga & gb) / (len(ga) + len(gb)) if ga and gb else 0.0
        # containment of the shorter text's words in the longer one (query vs. long description)
        short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        cont = len(short & long_) / len(short) if short else 0.0
        return max(jac, dice, 0.75 * cont)

    def score(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        qv = self._emb.embed([query])[0]
        dv = self._emb.embed(docs)
        cos = np.clip(dv @ qv, 0.0, 1.0)
        return [float(0.7 * self._sim(query, d) + 0.3 * c) for d, c in zip(docs, cos)]
