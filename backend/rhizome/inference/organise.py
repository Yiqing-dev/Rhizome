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

    def confirm(self, conf: float, reason: str) -> None:
        if self.topic.status != "candidate":
            self.rejected += 1
            return
        self._queue("confirm", "", "confirm_topic", {"key": self.topic.key}, conf, reason)

    def hide(self, conf: float, reason: str) -> None:
        if self.topic.status == "rejected":
            self.rejected += 1
            return
        self._queue("reject", "", "reject_entity", {"key": self.topic.key}, conf, reason)

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


# ---- the whole tree ---------------------------------------------------------------------------

TREE_TURNS = 12
TREE_MAX = 25  # proposals per run: a reviewable batch, not a rewrite of the library
TREE_KEY = "organise:tree"

TREE_SYSTEM = """You help organise the topic tree of a personal literature library. Topics are short labels that
papers and their assets are tagged with; `is_a` edges put a narrower topic under a broader one.
Candidate topics were proposed by exports and are not confirmed yet. Read the tree with list_topics
(page through it), and look closer with topic_info, shared_material and search_topics where a name
alone is ambiguous. Then propose the few changes that make the tree clearer:

- merges: two topics that are the same (synonyms, an acronym and its expansion, a translation).
- hierarchy: a topic that belongs under an existing broader topic (child, parent).
- groups: a new broader topic for two or more existing topics that clearly share a parent the tree
  lacks (give a short English noun-phrase name and a one-sentence definition).
- promote: candidate topics that are real, recurring topics worth confirming.
- hide: topics that are noise (too vague, a one-off phrase, not a topic at all).

Use topic keys exactly as listed. Each proposal has a confidence from 0 to 1 and a one-sentence
reason naming the evidence. Prefer few, well-founded proposals over many; empty lists are fine.
`summary` is two or three sentences on the state of the tree for the user."""

TREE_TOOLS = TOOL_DEFS + [{
    "name": "list_topics", "strict": True,
    "description": "Every topic, largest first, 60 at a time: key, name, status, how many items are tagged, parents.",
    "input_schema": {"type": "object", "properties": {"offset": {"type": "integer"}},
                     "required": ["offset"], "additionalProperties": False}}]

_PAIR = lambda a, b: {"type": "object", "additionalProperties": False,  # noqa: E731
                      "required": [a, b, "confidence", "reason"],
                      "properties": {a: {"type": "string"}, b: {"type": "string"}, **_ITEM}}
_ONE = {"type": "object", "additionalProperties": False, "required": ["key", "confidence", "reason"],
        "properties": {"key": {"type": "string"}, **_ITEM}}
TREE_SCHEMA = {"type": "object", "additionalProperties": False,
               "required": ["summary", "merges", "hierarchy", "groups", "promote", "hide"],
               "properties": {
                   "summary": {"type": "string"},
                   "merges": {"type": "array", "items": _PAIR("a", "b")},
                   "hierarchy": {"type": "array", "items": _PAIR("child", "parent")},
                   "groups": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False,
                       "required": ["name", "definition", "children", "confidence", "reason"],
                       "properties": {"name": {"type": "string"}, "definition": {"type": "string"},
                                      "children": {"type": "array", "items": {"type": "string"}}, **_ITEM}}},
                   "promote": {"type": "array", "items": _ONE},
                   "hide": {"type": "array", "items": _ONE}}}


class TreeProposals:
    """Per-topic checks reused for proposals that span the tree."""

    def __init__(self, s: Session, model: str):
        from ..pipeline.graph import Graph

        self.s, self.model, self.g = s, model, Graph(s)
        self._by: dict[int, Proposals] = {}
        self.rejected = 0

    def topic(self, key: str) -> Entity | None:
        g = self.g
        e = g.by_key(g.resolve_key(key)) if key else None
        if e is None and key:
            e = g.by_alias("topic", key.removeprefix("topic:"))
        return e if e is not None and e.type == "topic" and e.status != "rejected" else None

    def of(self, e: Entity) -> Proposals:
        if e.id not in self._by:
            self._by[e.id] = Proposals(self.s, e, self.model)
        return self._by[e.id]

    @property
    def queued(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self._by.values():
            for k, v in p.queued.items():
                out[k] = out.get(k, 0) + v
        return out

    def not_queued(self) -> int:
        return self.rejected + sum(p.rejected for p in self._by.values())

    def group(self, name: str, definition: str, children: list[str], conf: float, reason: str) -> None:
        from ..pipeline.canonicalize import free_key

        name = (name or "").strip()
        kids = []
        for k in children or []:
            e = self.topic(k)
            if e is not None and e.id not in {x.id for x in kids}:
                kids.append(e)
        if not name or len(name) > 80 or len(kids) < 2:
            self.rejected += 1
            return
        existing = self.g.by_key(free_key("topic", name)) or self.g.by_alias("topic", name)
        if existing is not None:  # the broader topic exists already: plain hierarchy proposals
            for kid in kids:
                if kid.id != existing.id:
                    self.of(kid).relation(existing.key, "parent", conf, reason)
            return
        new_key = free_key("topic", name)
        ops = [{"op": "create_topic", "args": {"name": name, "definition": (definition or "").strip()[:400] or None}}]
        ops += [{"op": "add_edge", "args": {"src": k.key, "dst": new_key, "type": "is_a"}} for k in kids]
        first = self.of(kids[0])
        first._queue("group", name, "batch", {}, conf, reason, value=name, ops=ops,
                     children=", ".join(k.canonical_name for k in kids))


def organise_tree(s: Session, client: Any = None, model: str | None = None) -> dict[str, Any]:
    """Run the tree organiser and queue its checked proposals (at most TREE_MAX)."""
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
    counts = dict(s.execute(select(Entity.status, func.count()).where(Entity.type == "topic").group_by(Entity.status)).all())
    total = sum(n for k, n in counts.items() if k != "rejected")
    if total < 2:
        return {"skipped": "fewer than two topics"}
    prompt = (f"The library has {total} topics ({counts.get('candidate', 0)} candidates). "
              "Organise the tree; start with list_topics at offset 0.")
    try:
        out = run_agent(client, model, TREE_SYSTEM, TREE_TOOLS, LibraryTools(s), prompt, TREE_SCHEMA,
                        max_turns=TREE_TURNS, max_tokens=16000)
    except AgentStopped as e:
        return {"stopped": str(e)}
    if out.get("error"):
        return {"error": out["error"]}
    tp = TreeProposals(s, model)
    budget = TREE_MAX

    def take() -> bool:
        nonlocal budget
        budget -= 1
        return budget >= 0

    for x in out.get("merges") or []:
        a = tp.topic(x.get("a", ""))
        if a is None:
            tp.rejected += 1
        elif take():
            tp.of(a).relation(x.get("b", ""), "merge", _confidence(x.get("confidence")), str(x.get("reason", "")))
    for x in out.get("hierarchy") or []:
        child = tp.topic(x.get("child", ""))
        if child is None:
            tp.rejected += 1
        elif take():
            tp.of(child).relation(x.get("parent", ""), "parent", _confidence(x.get("confidence")), str(x.get("reason", "")))
    for x in out.get("groups") or []:
        if take():
            tp.group(x.get("name", ""), x.get("definition", ""), x.get("children") or [],
                     _confidence(x.get("confidence")), str(x.get("reason", "")))
    for field_, method in (("promote", "confirm"), ("hide", "hide")):
        for x in out.get(field_) or []:
            t = tp.topic(x.get("key", ""))
            if t is None:
                tp.rejected += 1
            elif take():
                getattr(tp.of(t), method)(_confidence(x.get("confidence")), str(x.get("reason", "")))
    result = {"summary": str(out.get("summary", ""))[:1200], "queued": sum(tp.queued.values()), "by_what": tp.queued,
              "not_queued": tp.not_queued(), "model": model, "at": utcnow().isoformat(), "turns": out.get("turns")}
    row = s.get(KV, TREE_KEY)
    if row is None:
        s.add(KV(k=TREE_KEY, v=result))
    else:
        row.v = result
    s.flush()
    return result


def last_tree_run(s: Session) -> dict[str, Any] | None:
    row = s.get(KV, TREE_KEY)
    return row.v if row is not None and isinstance(row.v, dict) else None
