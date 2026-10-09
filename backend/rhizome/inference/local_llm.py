# SPDX-License-Identifier: Apache-2.0
"""Optional local small LLM (Qwen3.5-2B, Q4_K_M GGUF) through llama-cpp-python.

Only three narrow jobs, all with JSON-schema constrained output: is_a breadth judgement,
about / applicable_to classification for retro-tagging, and review-card generation.
"""

from __future__ import annotations

import json
from pathlib import Path

from .prompts import BREADTH, CARD, TOPIC_RELATION


class LocalLlamaBackend:
    name = "local"

    def __init__(self, model_path: Path):
        from llama_cpp import Llama

        self._llm = Llama(model_path=str(model_path), n_ctx=2048, verbose=False)

    def _ask(self, system: str, user: str, schema: dict) -> dict | None:
        out = self._llm.create_chat_completion(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object", "schema": schema},
            temperature=0.0,
            max_tokens=256,
        )
        try:
            return json.loads(out["choices"][0]["message"]["content"])
        except (KeyError, json.JSONDecodeError):
            return None

    def classify_topic_relation(self, asset_text, topic_def):
        system, schema = TOPIC_RELATION
        r = self._ask(system, f"TOPIC:\n{topic_def}\n\nASSET:\n{asset_text}", schema)
        return (r["relation"], float(r["confidence"])) if r else None

    def judge_breadth(self, a, b):
        system, schema = BREADTH
        r = self._ask(system, f"A: {a}\nB: {b}", schema)
        return (r["relation"], float(r["confidence"])) if r else None

    def make_card(self, asset_text):
        system, schema = CARD
        r = self._ask(system, asset_text, schema)
        return (r["q"], r["a"]) if r else None
