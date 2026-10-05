# SPDX-License-Identifier: Apache-2.0
"""Human decisions: recorded once, applied immediately, and re-applied last on every rebuild.

Payloads reference durable entity keys, never row ids. Every op is idempotent.

ops:
  merge          {from, into}
  distinct       {a, b}                       never propose merging these again
  split          {key, new_name, edges: [{src, dst, type}]}
  edge_status    {src, dst, type, status}     status: confirmed | rejected
  add_edge       {src, dst, type, attrs?, evidence?, extraction_id?, confidence?}
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
from ..text import norm
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
    payload = canonical_payload(g, op, payload)
    # a repeated request (double click, retried call) is the same decision, not a second row
    last = g.s.execute(select(HumanDecision).where(HumanDecision.revoked_at.is_(None))
                       .order_by(HumanDecision.id.desc()).limit(1)).scalar_one_or_none()
    if last is not None and last.op == op and last.payload == dict(payload):
        return last
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
    blank = [k for k in required if isinstance(p.get(k), str) and not p[k].strip()]
    if blank:
        raise DecisionError(f"{op}: {', '.join(blank)} must not be empty")
    for k in ("name", "alias", "text", "new_name"):
        if isinstance(p.get(k), str):
            p[k] = p[k].strip()
    if op in ("edge_status", "add_edge") and p["type"] not in EDGE_TYPES:
        raise DecisionError(f"unknown edge type {p['type']}; one of: {', '.join(EDGE_TYPES)}")
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
        raise DecisionError(f"entity {key} not found" + _hint(g, key))
    return e


def _hint(g: Graph, key: str) -> str:
    """'did you mean': entities of that type whose alias is the key's name part."""
    etype, _, name = key.partition(":")
    if not name or etype not in ("topic", "method", "dataset", "idea", "claim", "work", "organism", "modality"):
        return ""
    words = name.replace(":", " ").split()
    for n in range(len(words), 0, -1):  # the name, then shorter and shorter prefixes of it
        hit = g.by_alias(etype, " ".join(words[:n]))
        if hit is not None:
            return f" (did you mean {hit.key}?)"
    return ""


def canonical_payload(g: Graph, op: str, p: dict[str, Any]) -> dict[str, Any]:
    """Keys typed by hand: an alias or a differently cased key resolves to the real key before the
    decision is stored (a payload with a mistyped key would be replayed forever)."""
    out = dict(p)
    for k in ("key", "from", "into", "a", "b", "src", "dst", "work", "parent"):
        v = out.get(k)
        if not isinstance(v, str) or g.by_key(v) is not None:
            continue
        etype, _, name = v.partition(":")
        cand = None
        if name and etype in ("topic", "method", "dataset", "idea", "claim", "work"):
            cand = g.by_alias(etype, name) or g.by_key(f"{etype}:{name.lower()}")
        elif not name and k == "parent":
            cand = g.by_alias("topic", v)
        if cand is not None:
            out[k] = cand.key
    if op == "create_topic" and out.get("parent") and g.by_key(out["parent"]) is None \
            and g.by_alias("topic", out["parent"]) is None:
        raise DecisionError(f"parent topic {out['parent']} not found")
    return out


def _op_merge(g: Graph, p):
    into = g.by_key(p["into"])  # may itself be a redirect from an earlier merge
    if into is None:
        raise DecisionError(f"merge target {p['into']} not found")
    src = g.s.execute(select(Entity).where(Entity.key == p["from"])).scalar_one_or_none()
    if src is None:
        where = g.resolve_key(p["from"])
        if where == into.key:
            return True  # already merged there by an earlier decision (a replay, or a stale queue item)
        if where != p["from"]:
            raise DecisionError(f"merge source {p['from']} ended up in {where}, not {p['into']}")
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
    """Move the listed edges (and the aliases that name the new entity) from ``key`` to a new
    entity. The edges are resolved first: a split that matches nothing is an error, not a silent
    success with an empty new entity. The pair is remembered as distinct so auto-merge never folds
    them back (Graph.distinct_pairs reads split decisions too)."""
    from sqlalchemy import select as _select

    from ..db.models import EntityAlias

    orig = _need(g, p["key"])
    moves = []
    for sig in p["edges"]:
        src, dst = g.by_key(sig["src"]), g.by_key(sig["dst"])
        if src is None or dst is None:
            continue
        ed = g.edge(src, dst, sig["type"])
        if ed is not None and orig.id in (ed.src, ed.dst):
            moves.append((ed, src, dst))
    if not moves:
        raise DecisionError(f"split {p['key']}: none of the listed edges exist")
    new_key = p.get("new_key") or free_key(orig.type, p["new_name"])
    p["new_key"] = new_key
    new = g.by_key(new_key) or g.create(orig.type, new_key, p["new_name"],
                                       attrs={k: v for k, v in (orig.attrs or {}).items() if k != "reported"})
    for ed, src, dst in moves:
        ns = new if src.id == orig.id else src
        nd = new if dst.id == orig.id else dst
        if g.edge(ns, nd, ed.type) is None:
            ed.src, ed.dst = ns.id, nd.id
        else:
            g.s.delete(ed)
    # aliases that are really the new entity's name go with it (otherwise the next paper that
    # uses that name lands on the original again)
    wanted = {norm(p["new_name"]), *(norm(a) for a in p.get("aliases") or [])}
    have = set(g.s.execute(_select(EntityAlias.norm).where(EntityAlias.entity_id == new.id)).scalars())
    for al in g.s.execute(_select(EntityAlias).where(EntityAlias.entity_id == orig.id)).scalars().all():
        if al.norm in wanted and al.norm != norm(orig.canonical_name):
            if al.norm in have:
                g.s.delete(al)  # the new entity already carries this name
            else:
                al.entity_id = new.id
    g.s.flush()
    g.reindex(orig)
    g.reindex(new)
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
    ed = g.upsert_edge(src, dst, p["type"], attrs={**(p.get("attrs") or {}), "origin": "user"}, status="confirmed",
                       evidence=p.get("evidence"), extraction_id=p.get("extraction_id"),
                       confidence=float(p.get("confidence") or 1.0))
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
        if any(attrs.get(k) and attrs.get(k) != (e.attrs or {}).get(k) for k in attrs):
            # a redefined topic: the pending candidates were judged against the old definition
            g.s.execute(update(ReviewItem).where(ReviewItem.status == "pending", ReviewItem.kind == "retro_tag",
                                                 ReviewItem.payload["topic"].as_string() == e.key)
                        .values(status="obsolete", resolved_at=utcnow()))
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
    """The user's own idea connecting entities (often a useful weekly-synthesis pair): linked in
    the graph like a user insight from an export (applicable_to a topic, relates_to anything
    else), so it is not an orphan the next synthesis proposes again, and given a review card."""
    from ..i18n import _
    from .materialize import upsert_card

    r = resolve_free(g, "idea", p["text"], attrs={"origin": "user", "weight": 2.0, "links": p.get("links", [])},
                     origin="user")
    g.update_attrs(r.entity, origin="user", weight=2.0)
    linked, unresolved, names = [], [], []
    for k in p.get("links", []):
        t = g.by_key(k)
        if t is None or t.id == r.entity.id:
            unresolved.append(k)
            continue
        g.upsert_edge(r.entity, t, "applicable_to" if t.type == "topic" else "relates_to",
                      status="confirmed", attrs={"origin": "user"})
        linked.append(t.key)
        names.append(t.canonical_name)
    g.update_attrs(r.entity, links=linked, links_unresolved=unresolved)
    if names:
        upsert_card(g, r.entity.key, _("card.linked_idea_q", items=" ↔ ".join(n[:80] for n in names)),
                    r.entity.canonical_name, "user", priority=10)
    return True


def decisions_since(g: Graph, since: datetime) -> list[HumanDecision]:
    return list(g.s.execute(select(HumanDecision).where(HumanDecision.created_at >= since)).scalars())


def edges_of(g: Graph, e: Entity) -> list[Edge]:
    return list(g.s.execute(select(Edge).where((Edge.src == e.id) | (Edge.dst == e.id))).scalars())
