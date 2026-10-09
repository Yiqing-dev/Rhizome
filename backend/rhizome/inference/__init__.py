# SPDX-License-Identifier: Apache-2.0
"""Inference backends: where judgement that needs a generative model goes.

The kernel never calls an LLM API. Implementations share one interface:
  * ``queue``  (default) - returns None, the caller puts the item in the review queue, which the
                user or Claude Desktop (via MCP ``rhz_queue``/``rhz_decide``) works through.
  * ``local``  - Qwen3.5-2B GGUF via llama.cpp with JSON-schema constrained decoding.
  * ``anthropic`` - the Anthropic API (structured output), key from the credential store or
                ANTHROPIC_API_KEY; falls back to ``queue`` when the SDK or the key is missing.
  * plugins   - third-party packages registering ``rhizome.inference`` entry points.
                Off unless selected in settings.
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
_backend_for: tuple[str, str] | None = None  # the (backend, model) settings it was built for


def get_backend() -> InferenceBackend:
    global _backend, _backend_for
    s = get_settings()
    wanted = (s.inference_backend, s.anthropic_model)
    if _backend is not None and _backend_for == wanted:
        return _backend
    _backend_for = wanted
    if s.inference_backend == "local" and s.local_llm_path:
        from .local_llm import LocalLlamaBackend

        _backend = LocalLlamaBackend(s.local_llm_path)
    elif s.inference_backend == "anthropic":
        from ..secrets import get_secret
        from .anthropic_api import AnthropicBackend, available

        key = get_secret("anthropic_api_key")
        if available() and key:
            _backend = AnthropicBackend(s.anthropic_model, key)
        else:
            import logging

            logging.getLogger(__name__).warning(
                "inference_backend is anthropic but %s: judgements go to the review queue",
                "the anthropic package is not installed" if not available() else "no API key is set")
            _backend = QueueBackend()
    elif s.inference_backend not in ("queue", "local", "anthropic"):
        eps = {ep.name: ep for ep in entry_points(group="rhizome.inference")}
        _backend = eps[s.inference_backend].load()() if s.inference_backend in eps else QueueBackend()
    else:
        _backend = QueueBackend()
    return _backend


def reset_backend() -> None:
    global _backend, _backend_for
    _backend = None
    _backend_for = None
