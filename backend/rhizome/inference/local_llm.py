# SPDX-License-Identifier: Apache-2.0
"""Optional local small LLM (Qwen3.5-2B, Q4_K_M GGUF) through llama-cpp-python.

Only three narrow jobs, all with JSON-schema constrained output: is_a breadth judgement,
about / applicable_to classification for retro-tagging, and review-card generation.
"""

from __future__ import annotations

import json
from pathlib import Path


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
        r = self._ask(
            "Decide how a research asset relates to a topic. 'about': the asset is primarily on this topic. "
            "'applicable_to': it is not about the topic but could be used for it. 'none': unrelated.",
            f"TOPIC:\n{topic_def}\n\nASSET:\n{asset_text}",
            {"type": "object", "properties": {
                "relation": {"enum": ["about", "applicable_to", "none"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
             "required": ["relation", "confidence"]},
        )
        return (r["relation"], float(r["confidence"])) if r else None

    def judge_breadth(self, a, b):
        r = self._ask(
            "Compare two research topics. 'broader': A is a broader topic that contains B. "
            "'narrower': A is a sub-topic of B. 'none': neither.",
            f"A: {a}\nB: {b}",
            {"type": "object", "properties": {
                "relation": {"enum": ["broader", "narrower", "none"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
             "required": ["relation", "confidence"]},
        )
        return (r["relation"], float(r["confidence"])) if r else None

    def make_card(self, asset_text):
        r = self._ask(
            "Write one flashcard that can be answered without the paper. Be concrete and short.",
            asset_text,
            {"type": "object", "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
             "required": ["q", "a"]},
        )
        return (r["q"], r["a"]) if r else None
