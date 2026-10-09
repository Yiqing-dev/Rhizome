# SPDX-License-Identifier: Apache-2.0
"""The three narrow judgements every generative backend answers, as (system prompt, JSON schema).

Shared by the local small model and the Anthropic API backend so that both are asked exactly the
same question and must answer in the same shape (schemas are closed: no extra fields).
"""

from __future__ import annotations

TOPIC_RELATION = (
    "Decide how a research asset relates to a topic. 'about': the asset is primarily on this topic. "
    "'applicable_to': it is not about the topic but could be used for it. 'none': unrelated.",
    {"type": "object", "properties": {
        "relation": {"type": "string", "enum": ["about", "applicable_to", "none"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
     "required": ["relation", "confidence"], "additionalProperties": False},
)

BREADTH = (
    "Compare two research topics. 'broader': A is a broader topic that contains B. "
    "'narrower': A is a sub-topic of B. 'none': neither.",
    {"type": "object", "properties": {
        "relation": {"type": "string", "enum": ["broader", "narrower", "none"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
     "required": ["relation", "confidence"], "additionalProperties": False},
)

CARD = (
    "Write one flashcard that can be answered without the paper. Be concrete and short.",
    {"type": "object", "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
     "required": ["q", "a"], "additionalProperties": False},
)
