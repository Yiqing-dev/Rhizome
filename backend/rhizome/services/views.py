# SPDX-License-Identifier: Apache-2.0
"""Read models for the UI: entity cards, local graphs, topic pages, the topic map.

The full paper graph is never sent to the client; every view caps elements (~300)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, literal, select
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


EDGES_PER_TYPE = 50


def _edges_page(s: Session, entity_id: int, direction: str, etype: str | None, offset: int, limit: int):
    """(Edge, other Entity) rows of one direction; per type when etype is None (window function)."""
    me, other_col = (Edge.src, Edge.dst) if direction == "out" else (Edge.dst, Edge.src)
    rank_order = (case((Edge.status == "confirmed", 0), (Edge.status == "auto", 1), else_=2), Edge.id)
    if etype is not None:
        return s.execute(select(Edge, Entity).join(Entity, Entity.id == other_col)
                         .where(me == entity_id, Edge.type == etype).order_by(*rank_order)
                         .offset(offset).limit(limit)).all()
    rn = func.row_number().over(partition_by=Edge.type, order_by=rank_order).label("rn")
    ranked = select(Edge.id.label("eid"), rn).where(me == entity_id).subquery()
    return s.execute(select(Edge, Entity).join(ranked, ranked.c.eid == Edge.id)
                     .join(Entity, Entity.id == other_col)
                     .where(ranked.c.rn > offset, ranked.c.rn <= offset + limit).order_by(Edge.type, ranked.c.rn)).all()


def entity_edges(s: Session, entity_id: int, etype: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """One edge type of an entity, both directions, paged (the card shows the first ones)."""
    out = [_edge_view(ed, o, "out") for ed, o in _edges_page(s, entity_id, "out", etype, 0, offset + limit)]
    inn = [_edge_view(ed, o, "in") for ed, o in _edges_page(s, entity_id, "in", etype, 0, offset + limit)]
    both = (out + inn)[offset:offset + limit]
    total = sum(s.execute(select(func.count()).where(col == entity_id, Edge.type == etype)).scalar_one()
                for col in (Edge.src, Edge.dst))
    return {"type": etype, "edges": both, "total": total, "offset": offset}


def entity_card(s: Session, entity_id: int, touch_access: bool = True) -> dict[str, Any] | None:
    e = s.get(Entity, entity_id)
    if e is None:
        return None
    aliases = s.execute(select(EntityAlias).where(EntityAlias.entity_id == e.id)).scalars().all()
    edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # a hub (an organism, a popular method) has thousands of edges: per type, the first
    # EDGES_PER_TYPE (confirmed first) and the total; entity_edges() pages through the rest
    for direction in ("out", "in"):
        for ed, other in _edges_page(s, e.id, direction, None, 0, EDGES_PER_TYPE):
            edges[ed.type].append(_edge_view(ed, other, direction))
    counts: Counter = Counter()
    for col in (Edge.src, Edge.dst):
        for etype, n in s.execute(select(Edge.type, func.count()).where(col == e.id).group_by(Edge.type)).all():
            counts[etype] += n
    card: dict[str, Any] = {
        **summarize(e), "attrs": e.attrs or {}, "created_at": e.created_at.isoformat() if e.created_at else None,
        "aliases": [{"alias": a.alias, "lang": a.lang, "source": a.source} for a in aliases],
        "edges": dict(edges), "edge_counts": dict(counts),
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
                "user_insights": _insight_views(s, ex), "suspect": (ex.meta or {}).get("suspect", []),
                "raw": raw.decode("utf-8") if raw else None,
                "has_pdf": len(ex.input_hashes or []) > 1,
            })
        card["exports"] = exports
    if touch_access:
        touch(s, e.key)
    return card


HUB_TYPES = ("organism", "modality")


def _insight_views(s: Session, ex: Extraction) -> list[dict[str, Any]]:
    """The export's user insights with the entity each became: its key (to edit it) and its current
    wording (edit_text changes the entity; the export text stays as written)."""
    from ..pipeline.graph import Graph

    g = Graph(s)
    keys = (ex.meta or {}).get("insight_keys") or []
    out = []
    for i, ins in enumerate(ex.output.get("user_insights", [])):
        e = g.by_key(keys[i]) if i < len(keys) else g.by_alias("idea", ins.get("text", ""))
        view = dict(ins)
        if e is not None:
            view.update(key=e.key, entity_id=e.id, name=e.canonical_name, edited=bool((e.attrs or {}).get("edited")))
        out.append(view)
    return out


def neighbors(s: Session, entity_id: int, hops: int = 1, edge_types: list[str] | None = None,
              limit: int = MAX_ELEMENTS, offset: int = 0, include_rejected: bool = False) -> dict[str, Any]:
    """The local graph, paged in SQL: node ids of one or two hops are a subquery (never a Python
    list bound into IN, which Windows SQLite caps), hubs (organism, modality: everything is two
    hops away through them) are not expanded, and only the edges among the page's nodes are read."""
    from sqlalchemy import union

    hops = max(1, min(hops, 2))

    def edge_filter(q):
        if edge_types:
            q = q.where(Edge.type.in_(edge_types))
        if not include_rejected:
            q = q.where(Edge.status != "rejected")
        return q

    def adjacent(frontier):
        return union(edge_filter(select(Edge.dst.label("id")).where(Edge.src.in_(frontier))),
                     edge_filter(select(Edge.src.label("id")).where(Edge.dst.in_(frontier))))

    hop1 = adjacent(select(literal(entity_id))).subquery("hop1")
    reach = select(hop1.c.id)
    if hops == 2:
        expand = (select(hop1.c.id).join(Entity, Entity.id == hop1.c.id)
                  .where(Entity.type.notin_(HUB_TYPES), hop1.c.id != entity_id))
        hop2 = adjacent(expand).subquery("hop2")
        reach = union(select(hop1.c.id), select(hop2.c.id))
    nodes_sq = reach.subquery("reach")
    ids_q = select(nodes_sq.c.id).distinct().where(nodes_sq.c.id != entity_id)
    if not include_rejected:  # rejected assets (hallucinated, wrong) stay out of the graph view
        ids_q = ids_q.join(Entity, Entity.id == nodes_sq.c.id).where(Entity.status != "rejected")
    total = s.execute(select(func.count()).select_from(ids_q.subquery())).scalar_one() + 1
    budget = max(1, limit // 2)
    page_ids = [entity_id] + list(s.execute(ids_q.order_by(nodes_sq.c.id).offset(offset).limit(budget - 1)).scalars())
    ents = {e.id: e for e in s.execute(select(Entity).where(Entity.id.in_(page_ids))).scalars()}
    page_edges = list(s.execute(edge_filter(select(Edge).where(Edge.src.in_(page_ids), Edge.dst.in_(page_ids)))
                                .order_by(Edge.id).limit(limit)).scalars())
    return {
        "center": entity_id,
        "nodes": [{"id": i, "key": ents[i].key, "type": ents[i].type, "name": ents[i].canonical_name,
                   "status": ents[i].status} for i in page_ids if i in ents],
        "edges": [{"id": ed.id, "src": ed.src, "dst": ed.dst, "type": ed.type, "status": ed.status}
                  for ed in page_edges if ed.src in ents and ed.dst in ents],
        "total_nodes": total, "offset": offset, "has_more": offset + budget < total,
    }


PER_COLUMN = 150
TOPIC_COLUMNS = ("dataset", "method", "idea", "claim", "work")


def _topic_members(sub_ids: list[int]):
    """(entity id, role, via work) rows for a topic subtree, as a SQL subquery: entities linked to
    the topic directly, and assets of the papers on the topic. Never materialised as a Python id
    list (a broad topic has tens of thousands of members: SQLite caps bound variables)."""
    from sqlalchemy import literal, union_all

    direct = select(Edge.src.label("eid"), Edge.type.label("role"), literal(None).label("via")).where(
        Edge.dst.in_(sub_ids), Edge.type.in_(("about", "applicable_to")), Edge.status != "rejected")
    works = (select(Edge.src).join(Entity, Entity.id == Edge.src)
             .where(Edge.dst.in_(sub_ids), Edge.type.in_(("about", "applicable_to")), Edge.status != "rejected",
                    Entity.type == "work"))
    via = select(Edge.dst.label("eid"), Edge.type.label("role"), Edge.src.label("via")).where(
        Edge.src.in_(works), Edge.type.in_(SOURCE_EDGE_TYPES), Edge.status != "rejected")
    return union_all(direct, via).subquery()


def topic_assets(s: Session, topic_id: int, role: str | None = None, column: str | None = None,
                 offset: int = 0, limit: int = PER_COLUMN) -> dict[str, Any] | None:
    """Topic page: data / methods / ideas / claims / papers under a topic subtree, aggregated in
    SQL; each column returns at most ``limit`` items plus its total (``column`` + ``offset`` page
    one column)."""
    from sqlalchemy import and_, case, distinct, exists

    topic = s.get(Entity, topic_id)
    if topic is None or topic.type != "topic":
        return None
    sub = topic_subtree(s, topic_id)
    m = _topic_members(sub)
    roles = func.group_concat(distinct(m.c.role)).label("roles")
    nworks = func.count(distinct(m.c.via)).label("works")
    agg = select(m.c.eid, roles, nworks).group_by(m.c.eid)
    if role:
        agg = agg.having(func.sum(case((m.c.role == role, 1), else_=0)) > 0)
    agg = agg.subquery()
    contested = exists().where(and_(Edge.dst == Entity.id, Edge.type == "contradicts", Edge.status != "rejected"))
    base = (select(Entity, agg.c.roles, agg.c.works, Work.year, contested.label("contested"))
            .join(agg, agg.c.eid == Entity.id).outerjoin(Work, Work.entity_id == Entity.id)
            .where(Entity.status != "rejected"))
    totals = dict(s.execute(select(Entity.type, func.count()).join(agg, agg.c.eid == Entity.id)
                            .where(Entity.status != "rejected", Entity.type.in_(TOPIC_COLUMNS))
                            .group_by(Entity.type)).all())
    order = {"claim": (contested.desc(), agg.c.works.desc(), Entity.canonical_name),
             "work": (Work.year.desc(), Entity.canonical_name)}
    columns: dict[str, list[dict[str, Any]]] = {c: [] for c in TOPIC_COLUMNS}
    for col in ([column] if column in TOPIC_COLUMNS else TOPIC_COLUMNS):
        rows = s.execute(base.where(Entity.type == col)
                         .order_by(*order.get(col, (agg.c.works.desc(), Entity.canonical_name)))
                         .offset(offset if column else 0).limit(limit)).all()
        for e, rs, works, year, is_contested in rows:
            item = summarize(e)
            item.update(roles=sorted((rs or "").split(",")) if rs else [],
                        works=works or (1 if e.type == "work" else 0), contested=bool(is_contested), year=year)
            columns[col].append(item)
    children = s.execute(select(Entity).join(Edge, Edge.src == Entity.id).where(
        Edge.dst == topic_id, Edge.type == "is_a", Edge.status != "rejected")).scalars().all()
    parents = s.execute(select(Entity).join(Edge, Edge.dst == Entity.id).where(
        Edge.src == topic_id, Edge.type == "is_a", Edge.status != "rejected")).scalars().all()
    touch(s, topic.key)
    return {"topic": {**summarize(topic), "attrs": topic.attrs or {}},
            "children": [summarize(c) for c in children], "parents": [summarize(p) for p in parents],
            "subtree_size": len(sub), "columns": columns,
            "totals": {c: int(totals.get(c, 0)) for c in TOPIC_COLUMNS}, "limit": limit}


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

    from .. import inbox

    return {"entities": by_type, "edges": s.query(Edge).count(), "review_queue": queue,
            "review_queue_by_kind": by_kind, "cards_due": due_count(s), "inbox": inbox.status()}


def work_sources(s: Session, entity_id: int) -> list[dict[str, Any]]:
    return source_works(s, [entity_id]).get(entity_id, [])
