# SPDX-License-Identifier: Apache-2.0
"""Model selection. A model named in settings.json may not be loadable in this process: the
installed desktop app has no torch / sentence-transformers, while a pip install sharing the same
library may have them. That is reported (ModelUnavailable, model_problems) instead of silently
mixing vectors from two embedders in one index."""

from __future__ import annotations

from importlib.util import find_spec

from ..config import Settings, get_settings
from .base import Embedder, Nli, Reranker
from .fallback import HashingEmbedder, LexicalReranker

_cache: dict[str, object] = {}

BUILTIN = {"embedder": "hashing", "reranker": "lexical", "nli": "none"}
# what each optional model needs importable
NEEDS = {"embedder": "sentence_transformers", "reranker": "sentence_transformers", "nli": "transformers"}


class ModelUnavailable(RuntimeError):
    pass


class IndexStale(ModelUnavailable):
    """The library's vectors were made with another embedder than the one configured: a rebuild is
    needed (searching or adding with mixed vectors would silently find nothing or the wrong thing)."""


def _importable(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def model_problems(settings: Settings | None = None) -> list[dict[str, str]]:
    """Configured models this process cannot load: [{kind, model, missing}]."""
    s = settings or get_settings()
    out = []
    for kind, builtin in BUILTIN.items():
        model = getattr(s, kind)
        if model != builtin and not _importable(NEEDS[kind]):
            out.append({"kind": kind, "model": model, "missing": NEEDS[kind]})
    return out


def _require(kind: str, model: str) -> None:
    if not _importable(NEEDS[kind]):
        from ..i18n import _

        raise ModelUnavailable(_("ml.unavailable", kind=kind, model=model, module=NEEDS[kind]))


def reset_models() -> None:
    _cache.clear()


def get_embedder() -> Embedder:
    s = get_settings()
    key = f"emb:{s.embedder}"
    if key not in _cache:
        if s.embedder == "hashing":
            _cache[key] = HashingEmbedder()
        else:
            _require("embedder", s.embedder)
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
            _require("reranker", s.reranker)
            from .hf import CrossEncoderReranker

            _cache[key] = CrossEncoderReranker(s.reranker, s.models_dir)
    return _cache[key]  # type: ignore[return-value]


def get_nli() -> Nli | None:
    s = get_settings()
    if s.nli == "none":
        return None
    key = f"nli:{s.nli}"
    if key not in _cache:
        _require("nli", s.nli)
        from .hf import TransformersNli

        _cache[key] = TransformersNli(s.nli, s.models_dir)
    return _cache[key]  # type: ignore[return-value]
