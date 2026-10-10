# SPDX-License-Identifier: Apache-2.0
"""'Organise this topic': a Claude agent reads one topic in the library and proposes edits.

It may propose a definition (only when the topic has none), extra aliases (acronym expansions,
translations, spelling variants), parent / child topics and same-topic merges among topics that
already exist, and items tagged with the topic that do not belong there. Every proposal becomes a
``suggestion`` review item carrying one ordinary decision (``op`` + ``args``) and the agent's
confidence and reason. Nothing changes until the user applies it (one by one in the review queue,
or all confident ones at once); every applied suggestion is an undoable decision.

Proposals are checked against the library before they are queued: the other topic must exist, a
pair the user called different is never proposed again, an existing hierarchy edge or alias is not
proposed twice, a misfiled item must really be tagged with the topic. A proposal the user skipped
keeps its dedupe key, so running the organiser again does not bring it back.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import KV, Edge, Entity, EntityAlias, utcnow
from ..text import norm, sha256
from .agent import TOOLS, AgentStopped, LibraryTools, _confidence, make_client, run_agent

MAX_TURNS = 8
MAX_EACH = 8  # proposals per category and run

SYSTEM = """You help organise a personal literature library. Each topic is a short label that papers and
their assets (datasets, methods, ideas, claims) are tagged with. You are given one topic. Look at
what it covers here with the tools (topic_info, tagged_items, search_topics for neighbours,
shared_material to compare it with a neighbour), then propose only edits the library supports:

- definition: one plain sentence saying what the topic covers here (only when it has none; else "").
- aliases: other names people use for it (an acronym and its expansion, the Chinese or English name,
  a common spelling variant). Not broader or narrower terms.
- relations: existing topics (use their keys) that are the same topic (merge), a broader topic it
  belongs to (parent), or a narrower topic inside it (child). Related-but-different is not a relation.
- misfiled: items tagged with this topic that are clearly about something else (use their keys).

Give each proposal a confidence from 0 to 1 and a one-sentence reason naming the evidence. Propose
nothing in a category when nothing is warranted; an empty list is a good answer. `summary` is two
sentences on the state of the topic for the user."""

TOOL_DEFS = TOOLS + [{
    "name": "tagged_items", "strict": True,
    "description": "Everything tagged with a topic (papers with their one-line summary, and assets), "
                   "40 at a time; pass offset to page.",
    "input_schema": {"type": "object", "properties": {"key": {"type": "string"}, "offset": {"type": "integer"}},
                     "required": ["key", "offset"], "additionalProperties": False}}]

_ITEM = {"confidence": {"type": "number"}, "reason": {"type": "string"}}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["summary", "definition", "definition_confidence", "aliases", "relations", "misfiled"],
          "properties": {
              "summary": {"type": "string"},
              "definition": {"type": "string"},
              "definition_confidence": {"type": "number"},
              "aliases": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                     "required": ["alias", "confidence", "reason"],
                                                     "properties": {"alias": {"type": "string"}, **_ITEM}}},
              "relations": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                       "required": ["other", "relation", "confidence", "reason"],
                                                       "properties": {"other": {"type": "string"},
                                                                      "relation": {"type": "string", "enum": ["merge", "parent", "child"]},
                                                                      **_ITEM}}},
              "misfiled": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                      "required": ["key", "confidence", "reason"],
                                                      "properties": {"key": {"type": "string"}, **_ITEM}}}}}


def last_run_key(topic_key: str) -> str:
    return "organise:" + sha256(topic_key)[:40]


def last_run(s: Session, topic_key: str) -> dict[str, Any] | None:
    row = s.get(KV, last_run_key(topic_key))
    return row.v if row is not None and isinstance(row.v, dict) else None


def _tagged_count(s: Session, e: Entity) -> int:
    return s.execute(select(func.count()).select_from(Edge).where(
        (Edge.dst == e.id) | (Edge.src == e.id), Edge.status != "rejected")).scalar_one()


class Proposals:
    """Checks each proposal against the library and queues the ones that hold."""

    def __init__(self, s: Session, topic: Entity, model: str):
        from ..pipeline.graph import Graph

        self.s, self.topic, self.model = s, topic, model
        self.g = Graph(s)
        self.distinct = self.g.distinct_pairs()
        self.queued: dict[str, int] = {}
        self.rejected = 0
        self.names = {norm(a) for a in s.execute(select(EntityAlias.alias).where(
            EntityAlias.entity_id == topic.id)).scalars()} | {norm(topic.canonical_name)}

    def _queue(self, what: str, target: str, op: str, args: dict[str, Any], conf: float, reason: str,
               **shown: Any) -> None:
        t = self.topic
        payload = {"topic": t.key, "topic_name": t.canonical_name, "what": what, "op": op, "args": args, **shown,
                   "agent": {"action": "apply", "confidence": conf, "reason": reason[:600], "model": self.model,
                             "at": utcnow().isoformat()}}
        item = self.g.queue("suggestion", payload, dedupe=f"suggestion:{t.key}:{what}:{norm(target)[:200]}", score=conf)
        if item is not None:
            self.queued[what] = self.queued.get(what, 0) + 1

    def definition(self, text: str, conf: float) -> None:
        a = self.topic.attrs or {}
        text = (text or "").strip()
        if a.get("definition") or not 10 <= len(text) <= 400:
            return
        args = {"key": self.topic.key, "name": self.topic.canonical_name, "definition": text,
                "examples": a.get("examples") or [], "counter_examples": a.get("counter_examples") or []}
        self._queue("definition", "definition", "create_topic", args, conf, "", value=text)

    def alias(self, alias: str, conf: float, reason: str) -> None:
        alias = (alias or "").strip()
        if not alias or len(alias) > 80 or norm(alias) in self.names:
            self.rejected += 1
            return
        self.names.add(norm(alias))
        self._queue("alias", alias, "add_alias", {"key": self.topic.key, "alias": alias}, conf, reason, value=alias)

    def relation(self, other_key: str, relation: str, conf: float, reason: str) -> None:
        g, t = self.g, self.topic
        other = g.by_key(g.resolve_key(other_key)) if other_key else None
        if other is None and other_key:
            other = g.by_alias("topic", other_key.removeprefix("topic:"))
        if (other is None or other.type != "topic" or other.id == t.id or other.status == "rejected"
                or frozenset((t.key, other.key)) in self.distinct):
            self.rejected += 1
            return
        shown = {"key": other.key, "other_name": other.canonical_name}
        if relation == "merge":
            big, small = (t, other) if _tagged_count(self.s, t) >= _tagged_count(self.s, other) else (other, t)
            self._queue("merge", other.key, "merge", {"from": small.key, "into": big.key}, conf, reason,
                        into_name=big.canonical_name, **shown)
            return
        src, dst = (t, other) if relation == "parent" else (other, t)
        if g.edge(src, dst, "is_a") is not None or g.edge(dst, src, "is_a") is not None:
            self.rejected += 1
            return
        self._queue(relation, other.key, "add_edge", {"src": src.key, "dst": dst.key, "type": "is_a"},
                    conf, reason, **shown)

    def misfiled(self, item_key: str, conf: float, reason: str) -> None:
        g, t = self.g, self.topic
        item = g.by_key(g.resolve_key(item_key)) if item_key else None
        ed = None
        if item is not None:
            ed = self.s.execute(select(Edge).where(Edge.src == item.id, Edge.dst == t.id, Edge.type != "is_a",
                                                   Edge.status != "rejected")).scalars().first()
        if ed is None:
            self.rejected += 1
            return
        self._queue("misfiled", item.key, "edge_status",
                    {"src": item.key, "dst": t.key, "type": ed.type, "status": "rejected"}, conf, reason,
                    key=item.key, other_name=item.canonical_name, edge=ed.type)


def organise_topic(s: Session, topic: str, client: Any = None, model: str | None = None) -> dict[str, Any]:
    """Run the organiser on one topic (key, name or id) and queue its checked proposals."""
    from ..pipeline.graph import Graph

    st = get_settings()
    if client is None:
        if st.inference_backend != "anthropic":
            return {"skipped": "inference_backend is not anthropic"}
        from ..secrets import get_secret
        from .anthropic_api import available

        if not available():
            return {"skipped": "the anthropic package is not installed"}
        key = get_secret("anthropic_api_key")
        if not key:
            return {"skipped": "no API key is set"}
        client = make_client(key)
    model = model or st.anthropic_model
    g = Graph(s)
    t = s.get(Entity, int(topic)) if str(topic).isdigit() else (g.by_key(g.resolve_key(topic)) or g.by_alias("topic", topic))
    if t is None or t.type != "topic":
        raise LookupError(topic)
    a = t.attrs or {}
    prompt = (f"Topic: {t.canonical_name} (key: {t.key}, status: {t.status}).\n"
              f"It {'has the definition: ' + a['definition'] if a.get('definition') else 'has no definition yet'}.")
    try:
        out = run_agent(client, model, SYSTEM, TOOL_DEFS, LibraryTools(s), prompt, SCHEMA, max_turns=MAX_TURNS)
    except AgentStopped as e:
        return {"topic": t.key, "stopped": str(e)}
    if out.get("error"):
        return {"topic": t.key, "error": out["error"]}
    p = Proposals(s, t, model)
    p.definition(out.get("definition", ""), _confidence(out.get("definition_confidence")))
    for x in (out.get("aliases") or [])[:MAX_EACH]:
        p.alias(x.get("alias", ""), _confidence(x.get("confidence")), str(x.get("reason", "")))
    for x in (out.get("relations") or [])[:MAX_EACH]:
        if x.get("relation") in ("merge", "parent", "child"):
            p.relation(x.get("other", ""), x["relation"], _confidence(x.get("confidence")), str(x.get("reason", "")))
    for x in (out.get("misfiled") or [])[:MAX_EACH * 2]:
        p.misfiled(x.get("key", ""), _confidence(x.get("confidence")), str(x.get("reason", "")))
    result = {"topic": t.key, "summary": str(out.get("summary", ""))[:800], "queued": sum(p.queued.values()),
              "by_what": p.queued, "not_queued": p.rejected, "model": model, "at": utcnow().isoformat(),
              "turns": out.get("turns")}
    row = s.get(KV, last_run_key(t.key))
    if row is None:
        s.add(KV(k=last_run_key(t.key), v=result))
    else:
        row.v = result
    s.flush()
    return result
