# SPDX-License-Identifier: Apache-2.0
"""Review queue (audit): list pending items and turn answers into human decisions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import KV, ReviewItem, utcnow
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
    """Both sides' aliases, definition and three representative connections."""
    from .views import entity_card

    ctx = {}
    for side in ("a", "b", "key", "claim", "topic", "new_claim"):
        key = it.payload.get(side)
        if not isinstance(key, str):
            continue
        e = g.by_key(key)
        if e is None:
            continue
        card = entity_card(g.s, e.id, touch_access=False)
        conns = [x for es in card["edges"].values() for x in es][:3]
        ctx[side] = {"id": e.id, "name": e.canonical_name, "type": e.type,
                     "aliases": [a["alias"] for a in card["aliases"]][:8],
                     "definition": (e.attrs or {}).get("definition"),
                     "connections": [{"type": c["type"], "direction": c["direction"], "name": c["other"]["name"]}
                                     for c in conns]}
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
    p = it.payload
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
        _tune_synthesis(s, useful=action == "useful")
        if action == "useful":
            text = note or f"{p.get('a_name')} ↔ {p.get('b_name')}"
            made = decisions.record(g, "create_idea", {"text": text, "links": [p["a"], p["b"]]})
    if it.status == "pending":
        it.status = "resolved"
    it.resolved_at = utcnow()
    return {"id": it.id, "status": it.status, "decision_id": made.id if made else None}


def _tune_synthesis(s: Session, useful: bool) -> None:
    """User feedback nudges the similarity bar for future candidates."""
    from ..config import get_settings

    row = s.get(KV, "synthesis_sim")
    cur = (row.v or {}).get("value") if row else None
    cur = cur if cur is not None else get_settings().thresholds.synthesis_sim
    cur = min(0.95, max(0.2, cur + (-0.01 if useful else 0.01)))
    if row is None:
        s.add(KV(k="synthesis_sim", v={"value": cur}))
    else:
        row.v = {"value": cur}
