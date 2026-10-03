# SPDX-License-Identifier: Apache-2.0
"""Interfaces for the local judgement models. Each can be switched off independently."""

from __future__ import annotations

from typing import Literal, Protocol

import numpy as np

NliLabel = Literal["entailment", "neutral", "contradiction"]


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return float32 array (n, dim), rows L2-normalised."""


class Reranker(Protocol):
    name: str

    def score(self, query: str, docs: list[str]) -> list[float]:
        """Relevance / same-concept probability in [0, 1]."""


class Nli(Protocol):
    name: str

    def classify(self, premise: str, hypothesis: str) -> tuple[NliLabel, float]:
        ...
