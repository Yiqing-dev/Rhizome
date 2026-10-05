# SPDX-License-Identifier: Apache-2.0
"""Human decisions: recorded once, applied immediately, and re-applied last on every rebuild.

Payloads reference durable entity keys, never row ids. Every op is idempotent.

ops:
  merge          {from, into}
  distinct       {a, b}                       never propose merging these again
  split          {key, new_name, edges: [{src, dst, type}]}
  edge_status    {src, dst, type, status}     status: confirmed | rejected
  add_edge       {src, dst, type, attrs?}
  create_topic   {name, definition?, examples?, counter_examples?, parent?, aliases?}
  confirm_topic  {key}
  rename         {key, name}
  add_alias      {key, alias, lang?}
  edit_text      {key, text}                  e.g. fix a user insight that the model paraphrased badly
  create_idea    {text, links: [keys]}        e.g. from a useful weekly-synthesis pair
  retract        {work, extraction_ids?}      withdraw a paper's exports (or the replaced ones);
                                              takes effect on the rebuild the API queues
  reject_entity  {key}                        a hallucinated / wrong asset: hidden everywhere
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update

from ..db.models import EDGE_TYPES, Edge, Entity, Extraction, HumanDecision, ReviewCard, ReviewItem, utcnow
from .canonicalize import free_key, resolve_free
from .graph import Graph

log = logging.getLogger(__name__)

OPS = ("merge", "distinct", "split", "edge_status", "add_edge", "create_topic", "confirm_topic",
       "rename", "add_alias", "edit_text", "create_idea", "retract", "reject_entity")
NEEDS_REBUILD = ("retract",)  # ops whose effect on the graph only appears after a rebuild


class DecisionError(ValueError):
    pass


def record(g: Graph, op: str, payload: dict[str, Any]) -> HumanDecision:
    """Apply first, persist only if it took effect: a decision that cannot be applied now would be
    replayed on every rebuild and (for merges) poison the key redirect map."""
    if op not in OPS:
        raise DecisionError(f"unknown op {op}")
    _validate(op, payload)
    d = HumanDecision(op=op, payload=dict(payload))
    if not apply(g, d, strict=True):
        raise DecisionError(f"{op} had no effect")
    g.s.add(d)
    g.s.flush()
    if op == "merge":
        g.invalidate_redirects()
    return d


def revoke(g: Graph, decision_id: int) -> bool:
    """Undo = mark revoked; takes full effect on the next rebuild (which the API schedules).
    Returns False when it was already revoked (a repeated request changes nothing)."""
    d = g.s.get(HumanDecision, decision_id)
    if d is None:
        raise DecisionError("no such decision")
    if d.revoked_at is not None:
        return False
    d.revoked_at = utcnow()
    g.invalidate_redirects()
    if d.op == "retract":  # the exports come back; the rebuild the API queues re-materialises them
        ids = d.payload.get("extraction_ids") or []
        if ids:
            g.s.execute(update(Extraction).where(Extraction.id.in_(ids)).values(is_current=True))
    if d.op == "reject_entity":
        key = g.resolve_key(d.payload["key"])
        g.s.execute(update(ReviewCard).where(ReviewCard.entity_key == key).values(suspended=False))
    if d.op in ("merge", "distinct"):
        # the resolved queue item for this pair would block it from ever being suggested again
        a, b = sorted([d.payload.get("from") or d.payload.get("a"), d.payload.get("into") or d.payload.get("b")])
        g.s.execute(delete(ReviewItem).where(ReviewItem.dedupe_key.in_([f"merge:{a}|{b}", f"topic_relation:{a}|{b}"]),
                                             ReviewItem.status != "pending"))
    return True


def _validate(op: str, p: dict[str, Any]) -> None:
    required = {
        "merge": ("from", "into"), "distinct": ("a", "b"), "split": ("key", "new_name", "edges"),
        "edge_status": ("src", "dst", "type", "status"), "add_edge": ("src", "dst", "type"),
        "create_topic": ("name",), "confirm_topic": ("key",), "rename": ("key", "name"),
        "add_alias": ("key", "alias"), "edit_text": ("key", "text"), "create_idea": ("text",),
        "retract": ("work",), "reject_entity": ("key",),
    }[op]
    missing = [k for k in required if k not in p]
    if missing:
        raise DecisionError(f"{op}: missing {', '.join(missing)}")
    if op in ("edge_status", "add_edge") and p["type"] not in EDGE_TYPES:
        raise DecisionError(f"unknown edge type {p['type']}")
    if op == "edge_status" and p["status"] not in ("confirmed", "rejected", "auto"):
        raise DecisionError("status must be confirmed | rejected | auto")


def apply(g: Graph, d: HumanDecision, strict: bool = False, skipped: list[dict] | None = None) -> bool:
    """``strict`` (recording a new decision): any problem is an error for the caller. On replay a
    stale decision (its entity no longer exists after a model or threshold change, or a later merge
    turned its edge into a self-loop) is skipped and reported, never allowed to abort a rebuild."""
    p = d.payload
    try:
        fn = globals()[f"_op_{d.op}"]
        return bool(fn(g, p))
    except Exception as e:
        if strict:
            if isinstance(e, DecisionError):
                raise
            raise DecisionError(f"{d.op}: {e}") from e
        log.warning("decision %s (%s) not applied: %s", d.id, d.op, e)
        if skipped is not None:
            skipped.append({"id": d.id, "op": d.op, "reason": str(e)})
        return False


def apply_all(g: Graph, skipped: list[dict] | None = None) -> int:
    n = 0
    g.invalidate_redirects()
    for d in g.s.execute(select(HumanDecision).where(HumanDecision.revoked_at.is_(None))
                         .order_by(HumanDecision.id)).scalars().all():
        g.clock = d.created_at  # entities a decision creates keep the date the decision was made
        try:
            n += apply(g, d, skipped=skipped)
        finally:
            g.clock = None
    return n


# ---- ops -----------------------------------------------------------------------------

def _need(g: Graph, key: str) -> Entity:
    e = g.by_key(key)
    if e is None:
        raise DecisionError(f"entity {key} not found")
    return e


def _op_merge(g: Graph, p):
    into = g.by_key(p["into"])  # may itself be a redirect from an earlier merge
    if into is None:
        raise DecisionError(f"merge target {p['into']} not found")
    src = g.s.execute(select(Entity).where(Entity.key == p["from"])).scalar_one_or_none()
    if src is None:
        if g.resolve_key(p["from"]) != p["from"]:
            # already merged away by an earlier decision; nothing to do (a replay, or a stale queue item)
            return g.resolve_key(p["from"]) == into.key
        raise DecisionError(f"merge source {p['from']} not found")
    if src.id == into.id:
        raise DecisionError("cannot merge an entity into itself")
    if src.type != into.type:
        raise DecisionError(f"cannot merge {src.type} into {into.type}")
    g.merge(src, into)
    return True


def _op_distinct(g: Graph, p):
    _need(g, p["a"]), _need(g, p["b"])
    return True  # consulted by canonicalisation via Graph.distinct_pairs()


def _op_split(g: Graph, p):
    orig = _need(g, p["key"])
    new_key = p.get("new_key") or free_key(orig.type, p["new_name"])
    new = g.by_key(new_key) or g.create(orig.type, new_key, p["new_name"], attrs=dict(orig.attrs or {}))
    for sig in p["edges"]:
        src, dst = g.by_key(sig["src"]), g.by_key(sig["dst"])
        if src is None or dst is None:
            continue
        ed = g.edge(src, dst, sig["type"])
        if ed is None:
            continue
        ns = new if src.id == orig.id else src
        nd = new if dst.id == orig.id else dst
        if g.edge(ns, nd, ed.type) is None:
            ed.src, ed.dst = ns.id, nd.id
    g.s.flush()
    return True


def _op_edge_status(g: Graph, p):
    src, dst = _need(g, p["src"]), _need(g, p["dst"])
    ed = g.edge(src, dst, p["type"])
    if ed is None:
        if p["status"] != "confirmed":
            return False
        ed = g.upsert_edge(src, dst, p["type"], status="confirmed")
    ed.status = p["status"]
    return True


def _op_add_edge(g: Graph, p):
    src, dst = _need(g, p["src"]), _need(g, p["dst"])
    ed = g.upsert_edge(src, dst, p["type"], attrs={**(p.get("attrs") or {}), "origin": "user"}, status="confirmed")
    if ed is None:
        raise DecisionError("cannot link an entity to itself")
    ed.status = "confirmed"  # an explicit add overrides an earlier rejection (upsert never lowers 'rejected')
    return True


def _op_create_topic(g: Graph, p):
    attrs = {"definition": p.get("definition"), "examples": p.get("examples") or [],
             "counter_examples": p.get("counter_examples") or []}
    key = p.get("key") or free_key("topic", p["name"])
    e = g.by_key(key)
    if e is None:
        e = g.by_alias("topic", p["name"])
    if e is None:
        e = g.create("topic", key, p["name"], status="active", attrs=attrs, aliases=tuple(p.get("aliases") or ()))
    else:
        g.update_attrs(e, **attrs)
        e.status = "active"
        for a in p.get("aliases") or ():
            g.add_alias(e, a, source="user")
        g.reindex(e)
    if p.get("parent"):
        parent = g.by_key(p["parent"]) or g.by_alias("topic", p["parent"])
        if parent is not None:
            g.upsert_edge(e, parent, "is_a", status="confirmed")
    return True


def _op_confirm_topic(g: Graph, p):
    _need(g, p["key"]).status = "active"
    return True


def _op_rename(g: Graph, p):
    e = _need(g, p["key"])
    g.add_alias(e, e.canonical_name, source="user")
    e.canonical_name = p["name"]
    g.add_alias(e, p["name"], source="user")
    g.reindex(e)
    return True


def _op_add_alias(g: Graph, p):
    g.add_alias(_need(g, p["key"]), p["alias"], source="user", lang=p.get("lang"))
    return True


def _op_edit_text(g: Graph, p):
    e = _need(g, p["key"])
    g.update_attrs(e, original_text=(e.attrs or {}).get("original_text") or e.canonical_name, edited=True)
    e.canonical_name = p["text"]
    g.add_alias(e, p["text"], source="user")
    g.reindex(e)
    return True


def _op_retract(g: Graph, p):
    """Mark a paper's exports (or the listed ones) as not current: L0 and L1 stay, the next rebuild
    no longer materialises them. The ids are stored in the payload so a revoke restores exactly
    these, and a replay is a no-op."""
    if "extraction_ids" not in p:
        work = g.by_key(p["work"])
        keys = {p["work"]} | ({work.key} if work else set())
        p["extraction_ids"] = list(g.s.execute(select(Extraction.id).where(
            Extraction.work_key.in_(keys), Extraction.is_current, Extraction.kind.in_(("rxf", "openalex")))
        ).scalars())
    if not p["extraction_ids"]:
        raise DecisionError(f"no current exports for {p['work']}")
    g.s.execute(update(Extraction).where(Extraction.id.in_(p["extraction_ids"])).values(is_current=False))
    return True


def _op_reject_entity(g: Graph, p):
    e = _need(g, p["key"])
    if e.type == "work":
        raise DecisionError("a paper is withdrawn with retract, not rejected")
    e.status = "rejected"
    g.s.execute(update(ReviewCard).where(ReviewCard.entity_key == e.key).values(suspended=True))
    return True


def _op_create_idea(g: Graph, p):
    r = resolve_free(g, "idea", p["text"], attrs={"origin": "user", "weight": 2.0, "links": p.get("links", [])})
    g.update_attrs(r.entity, origin="user", links=p.get("links", []))
    for k in p.get("links", []):
        t = g.by_key(k)
        if t is not None and t.type == "topic":
            g.upsert_edge(r.entity, t, "applicable_to", status="confirmed", attrs={"origin": "user"})
    return True


def decisions_since(g: Graph, since: datetime) -> list[HumanDecision]:
    return list(g.s.execute(select(HumanDecision).where(HumanDecision.created_at >= since)).scalars())


def edges_of(g: Graph, e: Entity) -> list[Edge]:
    return list(g.s.execute(select(Edge).where((Edge.src == e.id) | (Edge.dst == e.id))).scalars())
