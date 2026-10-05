# SPDX-License-Identifier: Apache-2.0
"""Optional real models (pip install 'rhizome[models]'). Downloaded on first use into the models
directory, only the files the loaded backend needs (safetensors + tokenizer, never .bin pickles).

Which weights produced a library's vectors must be unambiguous: a model is loaded from one
resolved snapshot and named ``<key>@<commit7>``, so vectors from two revisions of "bge-m3" are
never mixed in one index. REVISIONS pins a commit per model (``RHIZOME_MODEL_REVISION_<KEY>``
overrides); None means the repository's current revision, recorded at download time."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

from ..config import get_settings

log = logging.getLogger(__name__)

MODEL_IDS = {
    "bge-m3": "BAAI/bge-m3",
    "bge-reranker-v2-m3": "BAAI/bge-reranker-v2-m3",
    "mdeberta": "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7",
}
REVISIONS: dict[str, str | None] = {"bge-m3": None, "bge-reranker-v2-m3": None, "mdeberta": None}
ALLOW_PATTERNS = ["*.json", "*.txt", "*.model", "*.safetensors", "tokenizer*", "sentencepiece*", "spm*",
                  "1_Pooling/*", "2_Normalize/*", "modules.json", "config_sentence_transformers.json"]


class ModelFilesMissing(RuntimeError):
    pass


def revision_for(key: str) -> str | None:
    return os.environ.get(f"RHIZOME_MODEL_REVISION_{key.upper().replace('-', '_')}") or REVISIONS.get(key)


def snapshot_path(key: str, cache_dir: Path, download: bool | None = None) -> Path:
    """The local snapshot folder for a model (``.../snapshots/<commit>``). Offline (setting or
    ``download=False``) only what is already there is used; a missing model names the command
    that fetches it, a network failure names the mirror variable."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    from ..i18n import _

    offline = get_settings().offline if download is None else not download
    try:
        return Path(snapshot_download(MODEL_IDS[key], revision=revision_for(key), cache_dir=str(cache_dir),
                                      allow_patterns=ALLOW_PATTERNS, local_files_only=offline))
    except LocalEntryNotFoundError as e:
        raise ModelFilesMissing(_("ml.files_missing", model=key, command=f"rhz models download {key}")) from e
    except Exception as e:  # noqa: BLE001 - network / proxy / mirror problems, all with one hint
        raise ModelFilesMissing(_("ml.download_failed", model=key, error=str(e)[:200])) from e


def versioned_name(key: str, path: Path) -> str:
    sha = path.name if path.parent.name == "snapshots" else "local"
    return f"{key}@{sha[:7]}"


class SentenceTransformerEmbedder:
    def __init__(self, key: str, cache_dir: Path):
        from sentence_transformers import SentenceTransformer

        path = snapshot_path(key, cache_dir)
        self.name = versioned_name(key, path)
        self._m = SentenceTransformer(str(path), device="cpu")
        self.dim = int(self._m.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.asarray(self._m.encode(texts, normalize_embeddings=True, batch_size=16), dtype=np.float32)


class CrossEncoderReranker:
    def __init__(self, key: str, cache_dir: Path):
        from sentence_transformers import CrossEncoder

        path = snapshot_path(key, cache_dir)
        self.name = versioned_name(key, path)
        self._m = CrossEncoder(str(path), device="cpu")

    def score(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        raw = self._m.predict([(query, d) for d in docs])
        return [float(1 / (1 + np.exp(-x))) if (x < 0 or x > 1) else float(x) for x in np.ravel(raw)]


class TransformersNli:
    def __init__(self, key: str, cache_dir: Path):
        from transformers import pipeline

        path = snapshot_path(key, cache_dir)
        self.name = versioned_name(key, path)
        self._p = pipeline("text-classification", model=str(path), top_k=None, device=-1)

    def classify(self, premise: str, hypothesis: str):
        out = self._p({"text": premise, "text_pair": hypothesis})
        if out and isinstance(out[0], list):
            out = out[0]
        best = max(out, key=lambda r: r["score"])
        return best["label"].lower(), float(best["score"])
