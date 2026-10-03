# SPDX-License-Identifier: Apache-2.0
"""Read models for the UI: entity cards, local graphs, topic pages, the topic map.

The full paper graph is never sent to the client; every view caps elements (~300)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import rawstore
from ..db.models import Edge, Entity, EntityAlias, Extraction, KV, ReviewItem, Work
from .recall import touch
from .search import SOURCE_EDGE_TYPES, source_works, summarize, topic_subtree

MAX_ELEMENTS = 300


def _edge_view(ed: Edge, other: Entity, direction: str) -> dict[str, Any]:
    return {"id": ed.id, "type": ed.type, "direction": direction, "status": ed.status,
            "confidence": ed.confidence, "evidence": ed.evidence, "attrs": ed.attrs or {},
            "other": {"id": other.id, "key": other.key, "type": other.type, "name": other.canonical_name,
                      "status": other.status}}


def entity_card(s: Session, entity_id: int, touch_access: bool = True) -> dict[str, Any] | None:
    e = s.get(Entity, entity_id)
    if e is None:
        return None
    aliases = s.execute(select(EntityAlias).where(EntityAlias.entity_id == e.id)).scalars().all()
    edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = s.execute(select(Edge, Entity).join(Entity, Entity.id == Edge.dst).where(Edge.src == e.id)).all()
    rows_in = s.execute(select(Edge, Entity).join(Entity, Entity.id == Edge.src).where(Edge.dst == e.id)).all()
    for ed, other in rows:
        edges[ed.type].append(_edge_view(ed, other, "out"))
    for ed, other in rows_in:
        edges[ed.type].append(_edge_view(ed, other, "in"))
    card: dict[str, Any] = {
        **summarize(e), "attrs": e.attrs or {}, "created_at": e.created_at.isoformat() if e.created_at else None,
        "aliases": [{"alias": a.alias, "lang": a.lang, "source": a.source} for a in aliases],
        "edges": dict(edges),
    }
    if e.type == "work":
        w = s.get(Work, e.id)
        card["work"] = {"openalex_id": w.openalex_id, "dois": w.dois, "year": w.year, "tier": w.tier} if w else None
        exports = []
        for ex in s.execute(select(Extraction).where(Extraction.work_key == e.key, Extraction.kind == "rxf",
                                                     Extraction.is_current).order_by(Extraction.id)).scalars():
            raw = rawstore.read(ex.input_hashes[0], "rxf") if ex.input_hashes else None
            o = ex.output
            exports.append({
                "extraction_id": ex.id, "depth": o.get("depth"), "prompt_version": ex.prompt_version,
                "schema_version": ex.schema_version, "created_at": ex.created_at.isoformat(),
                "tldr": o.get("tldr", []), "claims": o.get("claims", []), "issues": o.get("issues", []),
                "user_insights": o.get("user_insights", []), "suspect": (ex.meta or {}).get("suspect", []),
                "raw": raw.decode("utf-8") if raw else None,
                "has_pdf": len(ex.input_hashes or []) > 1,
            })
        card["exports"] = exports
    if touch_access:
        touch(s, e.key)
    return card


def neighbors(s: Session, entity_id: int, hops: int = 1, edge_types: list[str] | None = None,
              limit: int = MAX_ELEMENTS, offset: int = 0, include_rejected: bool = False) -> dict[str, Any]:
    hops = max(1, min(hops, 2))
    frontier = {entity_id}
    seen_nodes = {entity_id}
    edges: dict[int, Edge] = {}
    for _ in range(hops):
        q = select(Edge).where((Edge.src.in_(frontier)) | (Edge.dst.in_(frontier)))
        if edge_types:
            q = q.where(Edge.type.in_(edge_types))
        if not include_rejected:
            q = q.where(Edge.status != "rejected")
        nxt: set[int] = set()
        for ed in s.execute(q.order_by(Edge.id)).scalars():
            edges[ed.id] = ed
            for n in (ed.src, ed.dst):
                if n not in seen_nodes:
                    nxt.add(n)
                    seen_nodes.add(n)
        frontier = nxt
        if not frontier:
            break
    ordered_nodes = [entity_id] + sorted(seen_nodes - {entity_id})
    total = len(ordered_nodes)
    budget = max(1, limit // 2)
    page_nodes = set([entity_id] + ordered_nodes[1:][offset:offset + budget - 1])
    page_edges = [ed for ed in edges.values() if ed.src in page_nodes and ed.dst in page_nodes]
    ents = {e.id: e for e in s.execute(select(Entity).where(Entity.id.in_(page_nodes))).scalars()}
    return {
        "center": entity_id,
        "nodes": [{"id": i, "key": ents[i].key, "type": ents[i].type, "name": ents[i].canonical_name,
                   "status": ents[i].status} for i in page_nodes if i in ents],
        "edges": [{"id": ed.id, "src": ed.src, "dst": ed.dst, "type": ed.type, "status": ed.status}
                  for ed in page_edges][:limit],
        "total_nodes": total, "offset": offset, "has_more": offset + budget - 1 < total - 1,
    }


def topic_assets(s: Session, topic_id: int, role: str | None = None) -> dict[str, Any] | None:
    topic = s.get(Entity, topic_id)
    if topic is None or topic.type != "topic":
        return None
    sub = topic_subtree(s, topic_id)
    linked = s.execute(select(Edge.src, Edge.type, Edge.dst).where(
        Edge.dst.in_(sub), Edge.type.in_(("about", "applicable_to")), Edge.status != "rejected")).all()
    ids = {src for src, _, _ in linked}
    ents = {e.id: e for e in s.execute(select(Entity).where(Entity.id.in_(ids))).scalars()}
    work_ids = {i for i in ids if ents[i].type == "work"}
    roles: dict[int, set[str]] = defaultdict(set)
    for src, etype, _ in linked:
        roles[src].add(etype)

    # assets that come from works on this topic
    rows = s.execute(select(Edge.dst, Edge.type, Edge.src).where(
        Edge.src.in_(work_ids), Edge.type.in_(SOURCE_EDGE_TYPES), Edge.status != "rejected")).all()
    via_work: dict[int, set[int]] = defaultdict(set)
    for dst, etype, src in rows:
        roles[dst].add(etype)
        via_work[dst].add(src)
    all_ids = ids | set(via_work)
    ents.update({e.id: e for e in s.execute(select(Entity).where(Entity.id.in_(all_ids - set(ents)))).scalars()})

    contested = set(s.execute(select(Edge.dst).where(Edge.dst.in_(all_ids), Edge.type == "contradicts",
                                                     Edge.status != "rejected")).scalars())
    years = {w.entity_id: w.year for w in s.execute(select(Work).where(Work.entity_id.in_(work_ids))).scalars()}
    columns: dict[str, list[dict[str, Any]]] = {"dataset": [], "method": [], "idea": [], "claim": [], "work": []}
    for i in all_ids:
        e = ents.get(i)
        if e is None or e.type not in columns:
            continue
        if role and role not in roles[i]:
            continue
        item = summarize(e)
        item.update(roles=sorted(roles[i]), works=len(via_work.get(i, ())) or (1 if e.type == "work" else 0),
                    contested=i in contested, year=years.get(i))
        columns[e.type].append(item)
    for col, items in columns.items():
        if col == "claim":
            items.sort(key=lambda x: (not x["contested"], -x["works"], x["name"]))
        elif col == "work":
            items.sort(key=lambda x: (-(x["year"] or 0), x["name"]))
        else:
            items.sort(key=lambda x: (-x["works"], x["name"]))
    children = s.execute(select(Entity).join(Edge, Edge.src == Entity.id).where(
        Edge.dst == topic_id, Edge.type == "is_a", Edge.status != "rejected")).scalars().all()
    parents = s.execute(select(Entity).join(Edge, Edge.dst == Entity.id).where(
        Edge.src == topic_id, Edge.type == "is_a", Edge.status != "rejected")).scalars().all()
    touch(s, topic.key)
    return {"topic": {**summarize(topic), "attrs": topic.attrs or {}},
            "children": [summarize(c) for c in children], "parents": [summarize(p) for p in parents],
            "subtree_size": len(sub), "columns": columns}


def topic_map(s: Session, include_candidates: bool = False) -> dict[str, Any]:
    """Topic layer only, aggregated on the server."""
    q = select(Entity).where(Entity.type == "topic")
    if not include_candidates:
        q = q.where(Entity.status == "active")
    topics = {t.id: t for t in s.execute(q).scalars()}
    counts: dict[int, Counter] = defaultdict(Counter)
    if topics:
        rows = s.execute(select(Edge.dst, Entity.type, func.count()).join(Entity, Entity.id == Edge.src)
                         .where(Edge.dst.in_(topics), Edge.type.in_(("about", "applicable_to")),
                                Edge.status != "rejected")
                         .group_by(Edge.dst, Entity.type)).all()
        for dst, etype, n in rows:
            counts[dst][etype] += n
    ranked = sorted(topics, key=lambda t: -sum(counts[t].values()))[:MAX_ELEMENTS - 50]
    keep = set(ranked)
    comm = (s.get(KV, "communities") or KV(v={})).v or {}
    nodes = [{"id": t, "key": topics[t].key, "name": topics[t].canonical_name, "status": topics[t].status,
              "counts": dict(counts[t]), "size": sum(counts[t].values()), "community": comm.get(topics[t].key)}
             for t in ranked]
    edges = [{"src": ed.src, "dst": ed.dst, "status": ed.status}
             for ed in s.execute(select(Edge).where(Edge.type == "is_a", Edge.src.in_(keep), Edge.dst.in_(keep),
                                                    Edge.status != "rejected")).scalars()]
    return {"nodes": nodes, "edges": edges, "total_topics": len(topics)}


def topic_changes(s: Session, topic_id: int, since: datetime) -> dict[str, Any]:
    """New/changed material under a topic since a date (monthly digest input for Claude)."""
    sub = topic_subtree(s, topic_id)
    new_edges = s.execute(select(Edge, Entity).join(Entity, Entity.id == Edge.src).where(
        Edge.dst.in_(sub), Edge.created_at >= since, Edge.status != "rejected")).all()
    works = [e.id for ed, e in new_edges if e.type == "work"]
    assets = s.execute(select(Edge, Entity).join(Entity, Entity.id == Edge.dst).where(
        Edge.src.in_(works), Edge.type.in_(SOURCE_EDGE_TYPES), Edge.status != "rejected")).all()
    contradictions = [ed for ed, _ in assets if ed.type == "contradicts"]
    return {
        "topic_id": topic_id, "since": since.isoformat(),
        "new_works": [summarize(e) for ed, e in new_edges if e.type == "work"],
        "new_assets": [dict(summarize(e), edge=ed.type) for ed, e in assets],
        "new_direct": [dict(summarize(e), edge=ed.type) for ed, e in new_edges if e.type != "work"],
        "contradictions": len(contradictions),
    }


def home_stats(s: Session) -> dict[str, Any]:
    by_type = dict(s.execute(select(Entity.type, func.count()).group_by(Entity.type)).all())
    queue = s.execute(select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "pending")).scalar_one()
    by_kind = dict(s.execute(select(ReviewItem.kind, func.count()).where(ReviewItem.status == "pending")
                             .group_by(ReviewItem.kind)).all())
    from .cards import due_count

    return {"entities": by_type, "edges": s.query(Edge).count(), "review_queue": queue,
            "review_queue_by_kind": by_kind, "cards_due": due_count(s)}


def work_sources(s: Session, entity_id: int) -> list[dict[str, Any]]:
    return source_works(s, [entity_id]).get(entity_id, [])
