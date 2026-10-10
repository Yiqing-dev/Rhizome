# SPDX-License-Identifier: Apache-2.0
"""A Claude agent that judges how two topics relate, looking at the library before answering.

The single-call judgement sees only two names ("GRN inference" vs "regulatory networks"). This
agent can read what each topic covers here (definition, aliases, parents and children, the papers
and assets tagged with it) and what the two share, and then picks one of the review item's own
actions with a confidence and a one-line reason. It works on pending review items:

* ``topic_relation`` items (merge / A is part of B / B is part of A / different), and
* ``merge`` items whose two sides are topics.

The verdict is written onto the item (``payload["agent"]``) and remembered in the KV table by the
item's dedupe key, so a rebuild, which regenerates review items, re-attaches it without asking
again. Nothing is decided here: the user confirms each suggestion, or applies all confident ones
at once (``services.review.apply_agent``); either way it is an ordinary, undoable decision.

The library tools are read-only and local. Topic names, definitions and paper titles / tl;dr lines
go to the API; nothing else does.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import KV, Edge, Entity, EntityAlias, ReviewItem, utcnow
from ..text import sha256

log = logging.getLogger(__name__)

MAX_TURNS = 6  # requests per item: a few lookups, then the answer
DEFAULT_LIMIT = 25  # items per run
FALLBACK_BETA = "server-side-fallback-2026-07-01"

ACTIONS = {
    "topic_relation": ("merge", "a_is_a_b", "b_is_a_a", "distinct", "skip"),
    "merge": ("merge", "distinct", "skip"),
}

SYSTEM = """You judge how two research topics in a personal literature library relate. Topics are short
labels the user's papers and assets (datasets, methods, ideas, claims) are tagged with. Names alone
are often ambiguous, so look at what each topic covers in this library with the tools before
deciding: its definition and aliases, its parent and child topics, the papers and assets tagged
with it, and what the two topics share.

Answer with the JSON object: `action`, `confidence` (0-1: how sure you are given what you saw) and
`reason` (one or two sentences naming the evidence). Actions:
- merge: both labels name the same topic (synonyms, an acronym and its expansion, a translation).
- a_is_a_b: A is a narrower topic that lies inside B. b_is_a_a: B is a narrower topic inside A.
- distinct: different topics; neither contains the other. Closely related topics are still distinct.
- skip: the library does not hold enough to tell.
Only the actions listed for the question are allowed. Prefer distinct over a hierarchy when the
containment is partial, and keep the confidence low when the library has little on either topic."""

TOOLS = [
    {"name": "topic_info", "strict": True,
     "description": "What a topic covers in this library: definition, aliases, parent and child topics, "
                    "how many papers and assets are tagged with it, and examples of each.",
     "input_schema": {"type": "object", "properties": {"key": {"type": "string", "description": "topic key, e.g. 'topic:grn inference'"}},
                      "required": ["key"], "additionalProperties": False}},
    {"name": "shared_material", "strict": True,
     "description": "Papers and assets tagged with both topics, and any hierarchy edge already between them.",
     "input_schema": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                      "required": ["a", "b"], "additionalProperties": False}},
    {"name": "search_topics", "strict": True,
     "description": "Other topics in the library matching a query (to see the neighbourhood, e.g. whether "
                    "a broader topic already exists).",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"], "additionalProperties": False}},
]


class AgentStopped(RuntimeError):
    """The run cannot go on (no key, authentication, rate limit, network): items stay pending."""


def eligible(it: ReviewItem) -> bool:
    if it.kind == "topic_relation":
        return True
    p = it.payload or {}
    return it.kind == "merge" and str(p.get("a", "")).startswith("topic:") and str(p.get("b", "")).startswith("topic:")


def cache_key(dedupe_key: str) -> str:
    return "agent:" + sha256(dedupe_key)[:40]


def cached_verdict(s: Session, dedupe_key: str) -> dict[str, Any] | None:
    row = s.get(KV, cache_key(dedupe_key))
    return row.v if row is not None and isinstance(row.v, dict) else None


def _schema(kind: str) -> dict[str, Any]:
    return {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS[kind])},
        "confidence": {"type": "number"},
        "reason": {"type": "string"}},
        "required": ["action", "confidence", "reason"], "additionalProperties": False}


# ---- library tools (read-only) ---------------------------------------------------------------

def _clip(obj: Any, limit: int = 6000) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


class LibraryTools:
    def __init__(self, s: Session):
        from ..pipeline.graph import Graph

        self.s = s
        self.g = Graph(s)

    def _topic(self, key: str) -> Entity | None:
        e = self.g.by_key(self.g.resolve_key(key)) if key else None
        if e is None and key and not key.startswith("topic:"):
            e = self.g.by_alias("topic", key)
        return e if e is not None and e.type == "topic" else None

    def topic_info(self, key: str) -> str:
        e = self._topic(key)
        if e is None:
            return _clip({"error": f"no topic {key!r}"})
        s, a = self.s, e.attrs or {}
        aliases = s.execute(select(EntityAlias.alias, EntityAlias.lang).where(EntityAlias.entity_id == e.id)
                            .order_by(EntityAlias.id).limit(15)).all()
        live = Edge.status != "rejected"
        parents = s.execute(select(Entity.canonical_name).join(Edge, Edge.dst == Entity.id).where(
            Edge.src == e.id, Edge.type == "is_a", live).limit(10)).scalars().all()
        children = s.execute(select(Entity.canonical_name).join(Edge, Edge.src == Entity.id).where(
            Edge.dst == e.id, Edge.type == "is_a", live).limit(10)).scalars().all()
        counts = dict(s.execute(select(Entity.type, func.count(func.distinct(Entity.id))).join(
            Edge, Edge.src == Entity.id).where(Edge.dst == e.id, live, Edge.type != "is_a",
                                               Entity.status != "rejected").group_by(Entity.type)).all())
        tagged = s.execute(select(Entity, Edge.type).join(Edge, Edge.src == Entity.id).where(
            Edge.dst == e.id, live, Edge.type != "is_a", Entity.status != "rejected")
            .order_by(Entity.id).limit(60)).all()
        papers = [{"title": x.canonical_name, "year": (x.attrs or {}).get("year"), "edge": et,
                   "tldr": ((x.attrs or {}).get("tldr") or [None])[0]} for x, et in tagged if x.type == "work"][:6]
        assets = [{"type": x.type, "name": x.canonical_name[:200], "edge": et}
                  for x, et in tagged if x.type != "work"][:10]
        return _clip({"key": e.key, "name": e.canonical_name, "status": e.status,
                      "definition": a.get("definition"), "examples": a.get("examples"),
                      "counter_examples": a.get("counter_examples"),
                      "aliases": [{"alias": al, "lang": lg} for al, lg in aliases],
                      "parents": parents, "children": children, "tagged_counts": counts,
                      "papers": papers, "assets": assets})

    def shared_material(self, a: str, b: str) -> str:
        ta, tb = self._topic(a), self._topic(b)
        if ta is None or tb is None:
            return _clip({"error": f"no topic {a if ta is None else b!r}"})
        s, live = self.s, Edge.status != "rejected"
        both = select(Edge.src).where(Edge.dst == ta.id, live, Edge.type != "is_a").intersect(
            select(Edge.src).where(Edge.dst == tb.id, live, Edge.type != "is_a"))
        shared = s.execute(select(Entity).where(Entity.id.in_(both), Entity.status != "rejected")).scalars().all()
        hierarchy = s.execute(select(Edge.src, Edge.dst).where(Edge.type == "is_a", live, or_(
            (Edge.src == ta.id) & (Edge.dst == tb.id), (Edge.src == tb.id) & (Edge.dst == ta.id)))).all()
        works = [x for x in shared if x.type == "work"]
        return _clip({"shared_papers": len(works), "paper_examples": [x.canonical_name for x in works[:6]],
                      "shared_assets": len(shared) - len(works),
                      "asset_examples": [{"type": x.type, "name": x.canonical_name[:200]} for x in shared if x.type != "work"][:8],
                      "existing_is_a": ["A is part of B" if src == ta.id else "B is part of A" for src, _ in hierarchy]})

    def tagged_items(self, key: str, offset: int = 0) -> str:
        e = self._topic(key)
        if e is None:
            return _clip({"error": f"no topic {key!r}"})
        rows = self.s.execute(select(Entity, Edge.type).join(Edge, Edge.src == Entity.id).where(
            Edge.dst == e.id, Edge.status != "rejected", Edge.type != "is_a", Entity.status != "rejected")
            .order_by(Entity.type, Entity.id).offset(max(0, offset)).limit(40)).all()
        return _clip({"offset": offset, "items": [
            {"key": x.key, "type": x.type, "name": x.canonical_name[:160], "edge": et,
             **({"tldr": ((x.attrs or {}).get("tldr") or [None])[0]} if x.type == "work" else {})}
            for x, et in rows], "more": len(rows) == 40}, limit=9000)

    def search_topics(self, query: str) -> str:
        from ..services.search import Filters, search

        hits = search(self.s, query[:200], Filters(types=("topic",)), limit=10)
        return _clip([{"key": h["key"], "name": h["name"], "status": h.get("status")} for h in hits])

    def run(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        fn = {"topic_info": self.topic_info, "shared_material": self.shared_material,
              "search_topics": self.search_topics, "tagged_items": self.tagged_items}.get(name)
        if fn is None:
            return f"unknown tool {name}", True
        try:
            return fn(**args), False
        except TypeError as e:
            return f"bad arguments: {e}", True


# ---- the agent loop --------------------------------------------------------------------------

def make_client(api_key: str | None):
    import anthropic

    return anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120.0)


def run_agent(client: Any, model: str, system: str, tool_defs: list[dict[str, Any]], tools: LibraryTools,
              prompt: str, schema: dict[str, Any], max_turns: int = MAX_TURNS, effort: str = "medium",
              max_tokens: int = 8000) -> dict[str, Any]:
    """Lookups through the library tools, then one JSON answer in `schema`. Returns the parsed
    answer (with "turns") or {"error": ...}; raises AgentStopped when the run should stop."""
    import anthropic

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    for turn in range(max_turns):
        try:
            resp = client.beta.messages.create(
                model=model, max_tokens=max_tokens, system=system, tools=tool_defs, messages=messages,
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                cache_control={"type": "ephemeral"},  # later turns of one run reuse the prefix
                betas=[FALLBACK_BETA], fallbacks="default")
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError,
                anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            raise AgentStopped(f"{type(e).__name__}: {getattr(e, 'message', e)}") from e
        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})  # unchanged: append-only
            results: list[dict[str, Any]] = []
            for b in resp.content:
                if getattr(b, "type", None) == "tool_use":
                    out, err = tools.run(b.name, dict(b.input or {}))
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": out,
                                    **({"is_error": True} if err else {})})
            if turn == max_turns - 2:
                results.append({"type": "text", "text": "No more lookups are available: answer now."})
            messages.append({"role": "user", "content": results})
            continue
        if resp.stop_reason in ("refusal", "max_tokens"):
            return {"error": resp.stop_reason}
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        try:
            out = json.loads(text)
        except json.JSONDecodeError:
            return {"error": "unparseable answer"}
        if not isinstance(out, dict):
            return {"error": "unparseable answer"}
        return {**out, "turns": turn + 1}
    return {"error": "no answer within the lookup budget"}


def _confidence(v: Any) -> float:
    try:
        return round(min(1.0, max(0.0, float(v))), 3)
    except (TypeError, ValueError):
        return 0.0


class TopicAgent:
    def __init__(self, model: str, api_key: str | None = None, client: Any = None):
        self.model = model
        self._api_key = api_key
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = make_client(self._api_key)
        return self._client

    def judge(self, tools: LibraryTools, kind: str, p: dict[str, Any]) -> dict[str, Any]:
        """One item: returns {action, confidence, reason} or {error}. Raises AgentStopped when the
        run should stop (credentials, rate limit, network)."""
        allowed = ACTIONS[kind]
        question = ("Are these two topic labels the same topic?" if kind == "merge"
                    else "How do these two topics relate?")
        prompt = (f"{question}\nA: {p.get('a_name')} (key: {p.get('a')})\nB: {p.get('b_name')} (key: {p.get('b')})\n"
                  f"Allowed actions: {', '.join(allowed)}.")
        out = run_agent(self.client, self.model, SYSTEM, TOOLS, tools, prompt, _schema(kind))
        if out.get("error"):
            return {"error": out["error"]}
        if out.get("action") not in allowed:
            return {"error": "answer outside the allowed actions"}
        return {"action": out["action"], "confidence": _confidence(out.get("confidence")),
                "reason": str(out.get("reason", ""))[:600], "turns": out["turns"]}


def _annotate(it: ReviewItem, verdict: dict[str, Any]) -> None:
    payload = dict(it.payload or {})
    payload["agent"] = verdict
    it.payload = payload  # a new dict: JSON columns only notice reassignment


def review_with_agent(s: Session, limit: int = DEFAULT_LIMIT, item_ids: list[int] | None = None,
                      force: bool = False, agent: TopicAgent | None = None) -> dict[str, Any]:
    """Judge pending topic items that have no verdict yet (``force``: judge again)."""
    st = get_settings()
    if agent is None:
        if st.inference_backend != "anthropic":
            return {"skipped": "inference_backend is not anthropic"}
        from ..secrets import get_secret
        from .anthropic_api import available

        if not available():
            return {"skipped": "the anthropic package is not installed"}
        key = get_secret("anthropic_api_key")
        if not key:
            return {"skipped": "no API key is set"}
        agent = TopicAgent(st.anthropic_model, key)
    q = select(ReviewItem).where(ReviewItem.status == "pending", ReviewItem.kind.in_(tuple(ACTIONS)))
    if item_ids:
        q = q.where(ReviewItem.id.in_(item_ids))
    todo = [it for it in s.execute(q.order_by(ReviewItem.score.desc(), ReviewItem.id)).scalars()
            if eligible(it) and (force or not (it.payload or {}).get("agent"))]
    tools = LibraryTools(s)
    out: dict[str, Any] = {"judged": 0, "from_cache": 0, "failed": 0, "stopped": None}
    for it in todo:
        if out["judged"] >= limit:
            break
        hit = None if force else cached_verdict(s, it.dedupe_key)
        if hit is not None:
            _annotate(it, hit)
            out["from_cache"] += 1
            continue
        try:
            verdict = agent.judge(tools, it.kind, it.payload or {})
        except AgentStopped as e:
            out["stopped"] = str(e)
            log.warning("topic agent stopped: %s", e)
            break
        verdict = {**verdict, "model": agent.model, "at": utcnow().isoformat()}
        _annotate(it, verdict)
        row = s.get(KV, cache_key(it.dedupe_key))
        if row is None:
            s.add(KV(k=cache_key(it.dedupe_key), v=verdict))
        else:
            row.v = verdict
        out["judged"] += 1
        out["failed"] += 1 if verdict.get("error") else 0
        s.commit()  # each verdict was paid for: keep it even if a later item fails
    out["remaining"] = max(0, len(todo) - out["judged"] - out["from_cache"])
    return out


def pending_unjudged(s: Session) -> int:
    items = s.execute(select(ReviewItem).where(ReviewItem.status == "pending",
                                               ReviewItem.kind.in_(tuple(ACTIONS)))).scalars()
    return sum(1 for it in items if eligible(it) and not (it.payload or {}).get("agent"))
