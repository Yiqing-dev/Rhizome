# SPDX-License-Identifier: Apache-2.0
"""Inference backends: where judgement that needs a generative model goes.

The kernel never calls an LLM API. Implementations share one interface:
  * ``queue``  (default) - returns None, the caller puts the item in the review queue, which the
                user or Claude Desktop (via MCP ``rhz_queue``/``rhz_decide``) works through.
  * ``local``  - Qwen3.5-2B GGUF via llama.cpp with JSON-schema constrained decoding.
  * plugins   - third-party packages registering ``rhizome.inference`` entry points
                (e.g. an API plugin). Off unless selected in settings.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Literal, Protocol

from ..config import get_settings

Relation = Literal["about", "applicable_to", "none"]
Breadth = Literal["broader", "narrower", "none"]


class InferenceBackend(Protocol):
    name: str

    def classify_topic_relation(self, asset_text: str, topic_def: str) -> tuple[Relation, float] | None: ...

    def judge_breadth(self, a: str, b: str) -> tuple[Breadth, float] | None: ...

    def make_card(self, asset_text: str) -> tuple[str, str] | None: ...


class QueueBackend:
    name = "queue"

    def classify_topic_relation(self, asset_text, topic_def):
        return None

    def judge_breadth(self, a, b):
        return None

    def make_card(self, asset_text):
        return None


_backend: InferenceBackend | None = None


def get_backend() -> InferenceBackend:
    global _backend
    s = get_settings()
    if _backend is not None and _backend.name == s.inference_backend:
        return _backend
    if s.inference_backend == "local" and s.local_llm_path:
        from .local_llm import LocalLlamaBackend

        _backend = LocalLlamaBackend(s.local_llm_path)
    elif s.inference_backend not in ("queue", "local"):
        eps = {ep.name: ep for ep in entry_points(group="rhizome.inference")}
        _backend = eps[s.inference_backend].load()() if s.inference_backend in eps else QueueBackend()
    else:
        _backend = QueueBackend()
    return _backend


def reset_backend() -> None:
    global _backend
    _backend = None
