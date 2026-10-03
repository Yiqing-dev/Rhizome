# SPDX-License-Identifier: Apache-2.0
"""Optional real models (pip install 'rhizome[models]'). Downloaded on first use into the
models directory; prefers the ONNX Runtime backend on CPU when available."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MODEL_IDS = {
    "bge-m3": "BAAI/bge-m3",
    "bge-reranker-v2-m3": "BAAI/bge-reranker-v2-m3",
    "mdeberta": "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7",
}


def _backend_kwargs() -> dict:
    try:
        import onnxruntime  # noqa: F401

        return {"backend": "onnx"}
    except ImportError:
        return {}


class SentenceTransformerEmbedder:
    def __init__(self, key: str, cache_dir: Path):
        from sentence_transformers import SentenceTransformer

        self.name = key
        try:
            self._m = SentenceTransformer(MODEL_IDS[key], cache_folder=str(cache_dir), device="cpu",
                                          **_backend_kwargs())
        except Exception:  # ONNX export unavailable for this checkpoint -> torch
            log.info("falling back to torch backend for %s", key)
            self._m = SentenceTransformer(MODEL_IDS[key], cache_folder=str(cache_dir), device="cpu")
        self.dim = int(self._m.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.asarray(self._m.encode(texts, normalize_embeddings=True, batch_size=16), dtype=np.float32)


class CrossEncoderReranker:
    def __init__(self, key: str, cache_dir: Path):
        from sentence_transformers import CrossEncoder

        self.name = key
        self._m = CrossEncoder(MODEL_IDS[key], cache_folder=str(cache_dir), device="cpu")

    def score(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        raw = self._m.predict([(query, d) for d in docs])
        return [float(1 / (1 + np.exp(-x))) if (x < 0 or x > 1) else float(x) for x in np.ravel(raw)]


class TransformersNli:
    def __init__(self, key: str, cache_dir: Path):
        from transformers import pipeline

        self.name = key
        self._p = pipeline("text-classification", model=MODEL_IDS[key], model_kwargs={"cache_dir": str(cache_dir)},
                           top_k=None, device=-1)

    def classify(self, premise: str, hypothesis: str):
        out = self._p({"text": premise, "text_pair": hypothesis})
        if out and isinstance(out[0], list):
            out = out[0]
        best = max(out, key=lambda r: r["score"])
        return best["label"].lower(), float(best["score"])
