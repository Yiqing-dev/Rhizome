# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from ..config import get_settings
from .base import Embedder, Nli, Reranker
from .fallback import HashingEmbedder, LexicalReranker

_cache: dict[str, object] = {}


def reset_models() -> None:
    _cache.clear()


def get_embedder() -> Embedder:
    s = get_settings()
    key = f"emb:{s.embedder}"
    if key not in _cache:
        if s.embedder == "hashing":
            _cache[key] = HashingEmbedder()
        else:
            from .hf import SentenceTransformerEmbedder

            _cache[key] = SentenceTransformerEmbedder(s.embedder, s.models_dir)
    return _cache[key]  # type: ignore[return-value]


def get_reranker() -> Reranker:
    s = get_settings()
    key = f"rr:{s.reranker}"
    if key not in _cache:
        if s.reranker == "lexical":
            _cache[key] = LexicalReranker()
        else:
            from .hf import CrossEncoderReranker

            _cache[key] = CrossEncoderReranker(s.reranker, s.models_dir)
    return _cache[key]  # type: ignore[return-value]


def get_nli() -> Nli | None:
    s = get_settings()
    if s.nli == "none":
        return None
    key = f"nli:{s.nli}"
    if key not in _cache:
        from .hf import TransformersNli

        _cache[key] = TransformersNli(s.nli, s.models_dir)
    return _cache[key]  # type: ignore[return-value]
