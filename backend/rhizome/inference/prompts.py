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


# ---- reading a new paper (inference/reader.py) -------------------------------------------------

LANGUAGE_NAME = {"zh": "Chinese", "en": "English"}

READER_SYSTEM = (
    "You are the reviewer behind Rhizome, a personal literature-asset library. The user hands you one paper as a PDF. "
    "Work only from the paper's own text, figures and tables: never infer or complete numbers, accessions, "
    "evidence locations or repositories; when the paper does not say, say so. Free text is written in {language}; "
    "field names, enum values, topic names and Latin species names stay English; the paper title stays as printed."
)

READER_IDENTIFY = (
    "Identify this paper from its first pages: DOI as printed (or the arXiv identifier without version), the exact "
    "title, year, first author's surname and venue. Leave a field null when the paper does not give it."
)

READER_IDENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "doi": {"type": ["string", "null"]}, "arxiv_id": {"type": ["string", "null"]},
        "title": {"type": "string"}, "year": {"type": ["integer", "null"]},
        "first_author": {"type": ["string", "null"]}, "venue": {"type": ["string", "null"]},
    },
    "required": ["doi", "arxiv_id", "title", "year", "first_author", "venue"], "additionalProperties": False,
}

def review_prompt() -> str:
    """The user's own deep-review prompt (deep-review-v2), shipped verbatim in rhizome/data so the
    report the app produces follows the same steps as the one a chat Project produces."""
    from importlib import resources

    return resources.files("rhizome").joinpath("data", "deep-review-v2.md").read_text("utf-8")


READER_EXPORT = (
    "Export RXF for this paper now, following the export instructions and skeleton in your instructions exactly. "
    "Output only one yaml code block and nothing else. Set rxf_version: 1, exported_at: {today}, "
    'prompt_version: "{prompt_version}", instructions_version as the skeleton gives it, depth: deep, language: {language}. '
    "Prefer the canonical topic names from the vocabulary; create a new topic only when none fits. "
    "There was no human discussion: omit user_insights entirely and set every idea's origin to model."
)

READER_REPAIR = (
    "The YAML did not pass the Rhizome validator. Fix exactly what the report lists and output the complete "
    "corrected file again as one yaml code block and nothing else.\n\n{report}"
)
