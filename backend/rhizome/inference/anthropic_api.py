# SPDX-License-Identifier: Apache-2.0
"""Optional generative backend on the Anthropic API (the "API plugin" of the design).

Off by default. When selected it answers only the three narrow judgements of the inference
interface, each as one short structured-output request; paper text never leaves the machine for
anything else. The API key comes from ``rhizome.secrets`` (environment variable or the system
credential store), never from settings.json. Any failure answers None, which sends the item to the
review queue exactly as the queue backend would.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .prompts import BREADTH, CARD, TOPIC_RELATION

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
PAUSE_AFTER_FAILURE = 60.0  # seconds without requests after a rate limit / server / network error


def available() -> bool:
    try:
        import anthropic  # noqa: F401

        return True
    except ImportError:
        return False


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None, client: Any = None):
        self.model = model or DEFAULT_MODEL
        self._api_key = api_key
        self._client = client  # tests inject a fake; otherwise created on first use
        self._disabled: str | None = None  # an authentication / permission failure: say it once
        self._paused_until = 0.0
        self.last_error: str | None = None

    @property
    def client(self):
        if self._client is None:
            import anthropic

            # max_retries=0: a judgement that fails is queued for the user, not retried for minutes
            self._client = anthropic.Anthropic(api_key=self._api_key, max_retries=0, timeout=60.0)
        return self._client

    def _ask(self, system: str, user: str, schema: dict) -> dict | None:
        if self._disabled or time.monotonic() < self._paused_until:
            return None
        import anthropic

        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                # a safety refusal on a judgement is re-run on a fallback model inside the same call
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            self._disabled = self.last_error = f"{type(e).__name__}: {getattr(e, 'message', e)}"
            log.warning("Anthropic backend disabled until the key is fixed: %s", self._disabled)
            return None
        except anthropic.BadRequestError as e:  # a wrong model name, a rejected parameter: not transient
            self._disabled = self.last_error = f"BadRequestError: {getattr(e, 'message', e)}"
            log.warning("Anthropic backend disabled: %s", self._disabled)
            return None
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            self.last_error = f"{type(e).__name__}: {getattr(e, 'message', e)}"
            self._paused_until = time.monotonic() + PAUSE_AFTER_FAILURE
            log.warning("Anthropic request failed, items go to the review queue for a minute: %s", self.last_error)
            return None
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if not text:
            return None
        try:
            out = json.loads(text)
        except json.JSONDecodeError:
            return None
        return out if isinstance(out, dict) else None

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
