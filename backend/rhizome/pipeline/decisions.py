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
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select

from ..db.models import EDGE_TYPES, Edge, Entity, HumanDecision, utcnow
from .canonicalize import free_key, resolve_free
from .graph import Graph

log = logging.getLogger(__name__)

OPS = ("merge", "distinct", "split", "edge_status", "add_edge", "create_topic", "confirm_topic",
       "rename", "add_alias", "edit_text", "create_idea")


class DecisionError(ValueError):
    pass


def record(g: Graph, op: str, payload: dict[str, Any]) -> HumanDecision:
    if op not in OPS:
        raise DecisionError(f"unknown op {op}")
    _validate(op, payload)
    d = HumanDecision(op=op, payload=payload)
    g.s.add(d)
    g.s.flush()
    if op == "merge":
        g.invalidate_redirects()
    apply(g, d)
    return d


def revoke(g: Graph, decision_id: int) -> None:
    """Undo = mark revoked; takes full effect on the next rebuild (which the API schedules)."""
    d = g.s.get(HumanDecision, decision_id)
    if d is None:
        raise DecisionError("no such decision")
    d.revoked_at = utcnow()
    g.invalidate_redirects()


def _validate(op: str, p: dict[str, Any]) -> None:
    required = {
        "merge": ("from", "into"), "distinct": ("a", "b"), "split": ("key", "new_name", "edges"),
        "edge_status": ("src", "dst", "type", "status"), "add_edge": ("src", "dst", "type"),
        "create_topic": ("name",), "confirm_topic": ("key",), "rename": ("key", "name"),
        "add_alias": ("key", "alias"), "edit_text": ("key", "text"), "create_idea": ("text",),
    }[op]
    missing = [k for k in required if k not in p]
    if missing:
        raise DecisionError(f"{op}: missing {', '.join(missing)}")
    if op in ("edge_status", "add_edge") and p["type"] not in EDGE_TYPES:
        raise DecisionError(f"unknown edge type {p['type']}")
    if op == "edge_status" and p["status"] not in ("confirmed", "rejected", "auto"):
        raise DecisionError("status must be confirmed | rejected | auto")


def apply(g: Graph, d: HumanDecision) -> bool:
    p = d.payload
    try:
        fn = globals()[f"_op_{d.op}"]
        return bool(fn(g, p))
    except DecisionError:
        raise
    except Exception as e:  # a stale decision must never break a rebuild
        log.warning("decision %s (%s) not applied: %s", d.id, d.op, e)
        return False


def apply_all(g: Graph) -> int:
    n = 0
    g.invalidate_redirects()
    for d in g.s.execute(select(HumanDecision).where(HumanDecision.revoked_at.is_(None))
                         .order_by(HumanDecision.id)).scalars().all():
        n += apply(g, d)
    return n


# ---- ops -----------------------------------------------------------------------------

def _need(g: Graph, key: str) -> Entity:
    e = g.by_key(key)
    if e is None:
        raise LookupError(f"entity {key} not found")
    return e


def _op_merge(g: Graph, p):
    src = g.s.execute(select(Entity).where(Entity.key == p["from"])).scalar_one_or_none()
    into = _need(g, p["into"])
    if src is None or src.id == into.id:
        return False
    g.merge(src, into)
    return True


def _op_distinct(g: Graph, p):
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
    g.upsert_edge(src, dst, p["type"], attrs={**(p.get("attrs") or {}), "origin": "user"}, status="confirmed")
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
