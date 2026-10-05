# SPDX-License-Identifier: Apache-2.0
"""Review queue (audit): list pending items and turn answers into human decisions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import ReviewItem, utcnow
from ..pipeline import decisions
from ..pipeline.graph import Graph

ACTIONS = {
    "merge": ("merge", "distinct", "skip"),
    "topic_relation": ("merge", "a_is_a_b", "b_is_a_a", "distinct", "skip"),
    "contradiction": ("accept", "reject", "skip"),
    "retro_tag": ("about", "applicable_to", "none", "skip"),
    "synthesis": ("useful", "useless", "skip"),
}


def list_items(s: Session, kind: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    q = select(ReviewItem).where(ReviewItem.status == "pending")
    cq = select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "pending")
    if kind:
        q, cq = q.where(ReviewItem.kind == kind), cq.where(ReviewItem.kind == kind)
    items = s.execute(q.order_by(ReviewItem.score.desc(), ReviewItem.id).offset(offset).limit(limit)).scalars()
    g = Graph(s)
    out = []
    for it in items:
        d = {"id": it.id, "kind": it.kind, "payload": it.payload, "score": it.score,
             "actions": ACTIONS.get(it.kind, ("skip",)), "created_at": it.created_at.isoformat()}
        d["context"] = _context(g, it)
        out.append(d)
    return {"items": out, "total": s.execute(cq).scalar_one()}


def _context(g: Graph, it: ReviewItem) -> dict[str, Any]:
    """Both sides' aliases, definition and three representative connections (cheap queries: a
    queue page asks for this for every item, and a side may be a hub with thousands of edges)."""
    from sqlalchemy import or_

    from ..db.models import Edge, Entity, EntityAlias

    ctx = {}
    for side in ("a", "b", "key", "claim", "topic", "new_claim"):
        key = it.payload.get(side)
        if not isinstance(key, str):
            continue
        e = g.by_key(key)
        if e is None:
            continue
        aliases = g.s.execute(select(EntityAlias.alias).where(EntityAlias.entity_id == e.id)
                              .order_by(EntityAlias.id).limit(8)).scalars().all()
        conns = []
        for ed in g.s.execute(select(Edge).where(or_(Edge.src == e.id, Edge.dst == e.id), Edge.status != "rejected")
                              .order_by(Edge.id).limit(3)).scalars():
            out = ed.src == e.id
            other = g.s.get(Entity, ed.dst if out else ed.src)
            if other is not None:
                conns.append({"type": ed.type, "direction": "out" if out else "in", "name": other.canonical_name})
        ctx[side] = {"id": e.id, "name": e.canonical_name, "type": e.type, "aliases": list(aliases),
                     "definition": (e.attrs or {}).get("definition"), "connections": conns}
    return ctx


def resolve(s: Session, item_id: int, action: str, note: str | None = None) -> dict[str, Any]:
    it = s.get(ReviewItem, item_id)
    if it is None:
        raise LookupError(item_id)
    if it.status != "pending":
        return {"id": it.id, "status": it.status}
    if action not in ACTIONS.get(it.kind, ()):
        raise ValueError(f"action {action} not valid for {it.kind}")
    g = Graph(s)
    p = dict(it.payload)
    # keys in the payload may have been merged since the item was queued: follow redirects, and
    # drop the item if both sides already ended up as one entity
    for k in ("a", "b", "key", "topic", "claim", "work"):
        if isinstance(p.get(k), str):
            p[k] = g.resolve_key(p[k])
    if it.kind in ("merge", "topic_relation") and p["a"] == p["b"]:
        it.status, it.resolved_at = "obsolete", utcnow()
        return {"id": it.id, "status": it.status, "decision_id": None}
    try:
        made = _apply(s, g, it, p, action, note)
    except decisions.DecisionError as e:
        if "not found" not in str(e):
            raise
        # an entity of this item no longer exists (merged away, rejected, rebuilt differently):
        # the question is moot, not an error for the user
        it.status, it.resolved_at = "obsolete", utcnow()
        return {"id": it.id, "status": it.status, "decision_id": None}
    if it.status == "pending":
        it.status = "resolved"
    it.resolved_at = utcnow()
    return {"id": it.id, "status": it.status, "decision_id": made.id if made else None}


def _apply(s: Session, g: Graph, it: ReviewItem, p: dict[str, Any], action: str, note: str | None):
    made = None
    if action == "skip":
        it.status = "skipped"
    elif it.kind == "merge":
        if action == "merge":
            made = decisions.record(g, "merge", {"from": p["a"], "into": p["b"]})
        else:
            made = decisions.record(g, "distinct", {"a": p["a"], "b": p["b"]})
    elif it.kind == "topic_relation":
        if action == "merge":
            made = decisions.record(g, "merge", {"from": p["a"], "into": p["b"]})
        elif action == "a_is_a_b":
            made = decisions.record(g, "add_edge", {"src": p["a"], "dst": p["b"], "type": "is_a"})
        elif action == "b_is_a_a":
            made = decisions.record(g, "add_edge", {"src": p["b"], "dst": p["a"], "type": "is_a"})
        else:
            made = decisions.record(g, "distinct", {"a": p["a"], "b": p["b"]})
    elif it.kind == "contradiction":
        if action == "accept":
            made = decisions.record(g, "add_edge", {"src": p["work"], "dst": p["claim"], "type": "contradicts",
                                                    "attrs": {"evidence": p.get("evidence")}})
    elif it.kind == "retro_tag":
        if action in ("about", "applicable_to"):
            rel = action if p.get("type") == "work" else "applicable_to"
            made = decisions.record(g, "add_edge", {"src": p["key"], "dst": p["topic"], "type": rel})
    elif it.kind == "synthesis":
        from .synthesis import tune_threshold

        tune_threshold(s, useful=action == "useful")
        if action == "useful":
            text = note or f"{p.get('a_name')} ↔ {p.get('b_name')}"
            made = decisions.record(g, "create_idea", {"text": text, "links": [p["a"], p["b"]]})
    return made
