# SPDX-License-Identifier: Apache-2.0
"""Hybrid asset-level search: vector kNN + keyword (FTS5 trigram / ILIKE) fused with RRF,
filtered, then the top 50 reranked. Every hit carries its source papers and evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from ..db.models import Edge, Entity, EntityAlias, Work
from ..db.session import has_fts
from ..ml import get_reranker
from ..text import norm, tokens
from ..pipeline.graph import embed_texts, entity_text, knn

DEFAULT_TYPES = ("work", "dataset", "method", "idea", "claim", "topic")
SOURCE_EDGE_TYPES = ("proposes", "uses", "produces", "evaluates", "supports", "contradicts")
RRF_K = 60
RERANK_TOP = 50


@dataclass
class Filters:
    types: tuple[str, ...] = DEFAULT_TYPES
    edge_type: str | None = None
    organism: str | None = None
    modality: str | None = None
    year_min: int | None = None
    year_max: int | None = None
    tier: int | None = None
    topic: int | None = None  # topic entity id; its is_a subtree is included
    include_candidates: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


def _fts_query(q: str) -> str | None:
    terms = [t for t in norm(q).split() if len(t) >= 3]
    if not terms:
        return None
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


def keyword_ids(s: Session, q: str, types: Iterable[str], k: int = 200) -> list[int]:
    types = list(types)
    ids: list[int] = []
    fq = _fts_query(q)
    if fq and has_fts(s):
        rows = s.execute(text(
            "select f.entity_id from entity_fts f join entity e on e.id = f.entity_id "
            f"where entity_fts match :q and e.type in ({','.join(':t%d' % i for i in range(len(types)))}) "
            "order by bm25(entity_fts) limit :k"),
            {"q": fq, "k": k, **{f"t{i}": t for i, t in enumerate(types)}}).all()
        ids = [r[0] for r in rows]
    if len(ids) < k:
        # short CJK terms (< 3 chars) and the Postgres backend: alias / name substring match
        short = [t for t in tokens(q) if t] or [norm(q)]
        conds = [EntityAlias.norm.contains(t) for t in short[:6] if t]
        if conds:
            rows = s.execute(
                select(Entity.id).join(EntityAlias, EntityAlias.entity_id == Entity.id)
                .where(Entity.type.in_(types), or_(*conds)).distinct().limit(k)
            ).scalars().all()
            ids += [i for i in rows if i not in set(ids)]
    return ids[:k]


def topic_subtree(s: Session, root_id: int) -> list[int]:
    rows = s.execute(text(
        "with recursive sub(id) as (select :r union "
        "select e.src from edge e join sub on e.dst = sub.id where e.type = 'is_a' and e.status != 'rejected') "
        "select id from sub"), {"r": root_id}).all()
    return [r[0] for r in rows]


def source_works(s: Session, ids: Iterable[int]) -> dict[int, list[dict[str, Any]]]:
    """For each entity id, the works it comes from (with edge type and evidence)."""
    ids = list(ids)
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = s.execute(
        select(Edge.dst, Edge.type, Edge.evidence, Entity.id, Entity.canonical_name, Work.year)
        .join(Entity, Entity.id == Edge.src).join(Work, Work.entity_id == Entity.id)
        .where(Edge.dst.in_(ids), Edge.type.in_(SOURCE_EDGE_TYPES), Edge.status != "rejected")
    ).all()
    for dst, etype, ev, wid, wname, year in rows:
        out[dst].append({"work_id": wid, "title": wname, "year": year, "edge": etype, "evidence": ev})
    return out


def _entities_linked_to(s: Session, targets: set[int], edge_types: tuple[str, ...]) -> set[int]:
    if not targets:
        return set()
    return set(s.execute(select(Edge.src).where(Edge.dst.in_(targets), Edge.type.in_(edge_types),
                                                Edge.status != "rejected")).scalars())


def _named(s: Session, etype: str, name: str) -> set[int]:
    n = norm(name)
    return set(s.execute(select(Entity.id).join(EntityAlias, EntityAlias.entity_id == Entity.id)
                         .where(Entity.type == etype, EntityAlias.norm == n)).scalars())


def apply_filters(s: Session, ids: list[int], f: Filters) -> list[int]:
    if not ids:
        return ids
    ents = {e.id: e for e in s.execute(select(Entity).where(Entity.id.in_(ids))).scalars()}
    keep = [i for i in ids if i in ents and ents[i].type in f.types
            and (f.include_candidates or ents[i].status != "candidate")]
    needs_sources = any(v is not None for v in (f.organism, f.modality, f.year_min, f.year_max, f.tier, f.topic))
    if not needs_sources and not f.edge_type:
        return keep
    src = source_works(s, keep)

    def works_of(i: int) -> set[int]:
        return {i} if ents[i].type == "work" else {w["work_id"] for w in src[i]}

    if f.edge_type:
        keep = [i for i in keep if any(w["edge"] == f.edge_type for w in src[i])]
    for etype, name, rel in (("organism", f.organism, "of_organism"), ("modality", f.modality, "of_modality")):
        if name:
            targets = _named(s, etype, name)
            linked = _entities_linked_to(s, targets, (rel,))
            keep = [i for i in keep if i in linked or works_of(i) & linked]
    if f.year_min or f.year_max or f.tier:
        wids = {w for i in keep for w in works_of(i)}
        rows = {w.entity_id: w for w in s.execute(select(Work).where(Work.entity_id.in_(wids))).scalars()}

        def ok(w: int) -> bool:
            r = rows.get(w)
            if r is None:
                return False
            if f.year_min and (r.year is None or r.year < f.year_min):
                return False
            if f.year_max and (r.year is None or r.year > f.year_max):
                return False
            return not (f.tier and r.tier < f.tier)

        keep = [i for i in keep if any(ok(w) for w in works_of(i))]
    if f.topic:
        sub = set(topic_subtree(s, f.topic))
        linked = _entities_linked_to(s, sub, ("about", "applicable_to"))
        keep = [i for i in keep if i in sub or i in linked or works_of(i) & linked]
    return keep


def search(s: Session, q: str, f: Filters | None = None, limit: int = 20, offset: int = 0,
           rerank: bool = True) -> list[dict[str, Any]]:
    f = f or Filters()
    q = q.strip()
    if not q:
        return []
    qvec = embed_texts(s, [q])[0]
    vec_hits = [i for i, _ in knn(s, qvec, types=f.types, k=200)]
    kw_hits = keyword_ids(s, q, f.types, k=200)
    fused: dict[int, float] = {}
    for ranked in (vec_hits, kw_hits):
        for r, i in enumerate(ranked):
            fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + r + 1)
    order = sorted(fused, key=lambda i: -fused[i])
    order = apply_filters(s, order, f)
    ents = {e.id: e for e in s.execute(select(Entity).where(Entity.id.in_(order[:max(RERANK_TOP, offset + limit)])))
            .scalars()}
    head = [i for i in order if i in ents]
    scores = {i: fused[i] for i in order}
    relevance: dict[int, float] = {}
    if rerank and head:
        top = head[:RERANK_TOP]
        rr = get_reranker().score(q, [entity_text(ents[i]) for i in top])
        for i, sc in zip(top, rr):
            relevance[i] = sc
            scores[i] = 1.0 + sc  # reranked results always above the un-reranked tail
        head = sorted(top, key=lambda i: -scores[i]) + head[RERANK_TOP:]
    page = head[offset:offset + limit]
    src = source_works(s, page)
    return [dict(summarize(ents[i], scores[i], src.get(i, [])), relevance=round(relevance[i], 4) if i in relevance else None)
            for i in page]


def summarize(e: Entity, score: float | None = None, sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    a = e.attrs or {}
    return {
        "id": e.id, "key": e.key, "type": e.type, "name": e.canonical_name, "status": e.status,
        "external_id": e.external_id, "score": round(score, 4) if score is not None else None,
        "origin": a.get("origin"), "sources": sources or [],
    }
