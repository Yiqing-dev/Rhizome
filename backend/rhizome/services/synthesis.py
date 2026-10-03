# SPDX-License-Identifier: Apache-2.0
"""Weekly synthesis: asset pairs that are semantically close, have no path within 2 hops and
sit in different communities - cross-field links you have not made yet. Explanations are written
by Claude through MCP, not generated locally."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import KV, Edge, Embedding, Entity, ReviewItem, utcnow
from ..ml import get_embedder
from ..pipeline.graph import Graph, knn
from .search import summarize

ASSETS = ("dataset", "method", "idea", "claim")


def _two_hop(s: Session, entity_id: int) -> set[int]:
    seen = {entity_id}
    frontier = {entity_id}
    for _ in range(2):
        rows = s.execute(select(Edge.src, Edge.dst).where(
            ((Edge.src.in_(frontier)) | (Edge.dst.in_(frontier))), Edge.status != "rejected")).all()
        nxt = {n for r in rows for n in r} - seen
        # do not walk through hubs (organism/modality): everything is 2 hops via "human"
        hubs = set(s.execute(select(Entity.id).where(Entity.id.in_(nxt), Entity.type.in_(("organism", "modality"))))
                   .scalars())
        seen |= nxt
        frontier = nxt - hubs
    return seen


def current_threshold(s: Session) -> float:
    row = s.get(KV, "synthesis_sim")
    return (row.v or {}).get("value") if row and row.v else get_settings().thresholds.synthesis_sim


def generate_candidates(s: Session, days: int = 7, max_items: int = 20) -> int:
    since = utcnow() - timedelta(days=days)
    th = current_threshold(s)
    comm = (s.get(KV, "communities") or KV(v={})).v or {}
    g = Graph(s)
    model = get_embedder().name
    recent = s.execute(select(Entity, Embedding.vec).join(Embedding, Embedding.entity_id == Entity.id)
                       .where(Entity.type.in_(ASSETS), Entity.created_at >= since, Embedding.model == model)).all()
    made = 0
    for e, vec in recent:
        g_two: set[int] | None = None
        for nid, sim in knn(s, np.frombuffer(vec, dtype=np.float32), types=ASSETS, k=10, exclude={e.id}):
            if sim < th:
                break
            other = g.by_id(nid)
            if other is None:
                continue
            if comm and comm.get(e.key) is not None and comm.get(e.key) == comm.get(other.key):
                continue
            g_two = g_two or _two_hop(s, e.id)
            if nid in g_two:
                continue
            a, b = sorted([e.key, other.key])
            if g.queue("synthesis", {"a": e.key, "b": other.key, "a_name": e.canonical_name[:300],
                                     "b_name": other.canonical_name[:300], "a_type": e.type, "b_type": other.type,
                                     "score": round(sim, 4)},
                       dedupe=f"synthesis:{a}|{b}", score=sim):
                made += 1
            if made >= max_items:
                return made
    return made


def weekly_digest(s: Session, days: int = 7) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    pairs = s.execute(select(ReviewItem).where(ReviewItem.kind == "synthesis", ReviewItem.status == "pending")
                      .order_by(ReviewItem.score.desc()).limit(30)).scalars().all()
    contra = s.execute(select(Edge).where(Edge.type == "contradicts", Edge.created_at >= since,
                                          Edge.status != "rejected")).scalars().all()
    pending_contra = s.execute(select(ReviewItem).where(ReviewItem.kind == "contradiction",
                                                        ReviewItem.status == "pending")).scalars().all()
    g = Graph(s)
    pairs_out = []
    for it in pairs:
        a, b = g.by_key(it.payload["a"]), g.by_key(it.payload["b"])
        if a and b:
            pairs_out.append({"item_id": it.id, "score": it.score, "a": summarize(a), "b": summarize(b)})
    contested = []
    for ed in contra:
        w, c = g.by_id(ed.src), g.by_id(ed.dst)
        if w and c:
            contested.append({"work": summarize(w), "claim": summarize(c), "evidence": ed.evidence})
    return {"since": since.isoformat(), "threshold": current_threshold(s), "pairs": pairs_out,
            "contradictions": contested,
            "contradictions_pending_review": [{"item_id": i.id, **i.payload} for i in pending_contra]}
