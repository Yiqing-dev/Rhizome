# SPDX-License-Identifier: Apache-2.0
"""Weekly synthesis: asset pairs that are semantically close, have no path within 2 hops and
sit in different communities - cross-field links you have not made yet. Explanations are written
by Claude through MCP, not generated locally."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import KV, Edge, Entity, ReviewItem, utcnow
from ..ml import get_embedder
from ..pipeline.graph import VECTORS, Graph
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


DELTA_KEY = "synthesis_delta"   # {embedder: learned offset} on top of thresholds.synthesis_sim
MAX_DELTA = 0.15
TARGET_USEFUL = 0.2             # the bar settles where about 1 in 5 suggested pairs is useful
STEP = 0.02


def _delta(s: Session) -> float:
    row = s.get(KV, DELTA_KEY)
    d = ((row.v or {}) if row else {}).get(get_embedder().name, 0.0)
    return max(-MAX_DELTA, min(MAX_DELTA, float(d or 0.0)))


def current_threshold(s: Session) -> float:
    """The configured similarity bar plus what feedback has taught it, per embedder (scores of two
    models are not comparable) and bounded, so it can neither drift to 0.95 nor hide the setting."""
    return round(get_settings().thresholds.synthesis_sim + _delta(s), 4)


def tune_threshold(s: Session, useful: bool) -> float:
    """Feedback on a suggested pair: useful lowers the bar, useless raises it, weighted so the
    bar is stable when TARGET_USEFUL of the suggestions are useful."""
    d = _delta(s) + (-STEP * (1 - TARGET_USEFUL) if useful else STEP * TARGET_USEFUL)
    d = max(-MAX_DELTA, min(MAX_DELTA, d))
    row = s.get(KV, DELTA_KEY)
    v = dict(row.v or {}) if row else {}
    v[get_embedder().name] = round(d, 4)
    if row is None:
        s.add(KV(k=DELTA_KEY, v=v))
    else:
        row.v = v
    return current_threshold(s)


def reset_threshold(s: Session) -> float:
    row = s.get(KV, DELTA_KEY)
    if row is not None:
        v = dict(row.v or {})
        v.pop(get_embedder().name, None)
        row.v = v
    return current_threshold(s)


MAX_SOURCES = 2000
PER_SOURCE = 2
CHUNK = 256


def generate_candidates(s: Session, days: int = 7, max_items: int = 20, since: datetime | None = None) -> int:
    """Pairs (recent asset, any asset) above the similarity bar, in different communities and not
    within two hops, best first and at most PER_SOURCE per source. ``since`` is the watermark of the
    last run (assets added between runs are never skipped); ``days`` is the fallback window. One
    matrix multiply per chunk of sources instead of one knn per asset, and the graph checks run
    only for the pairs that are good enough; the queue is written afterwards, in one short step."""
    since = since or (utcnow() - timedelta(days=days))
    th = current_threshold(s)
    comm = (s.get(KV, "communities") or KV(v={})).v or {}
    g = Graph(s)
    model = get_embedder().name
    ids, types, mat = VECTORS.get(s, model)
    if len(ids) == 0:
        return 0
    recent = s.execute(select(Entity.id).where(Entity.type.in_(ASSETS), Entity.created_at >= since)
                       .order_by(Entity.created_at.desc(), Entity.id.desc()).limit(MAX_SOURCES)).scalars().all()
    pos = {int(i): k for k, i in enumerate(ids)}
    src_rows = [pos[i] for i in recent if i in pos]
    asset_mask = np.isin(types, list(ASSETS))
    pairs: list[tuple[float, int, int]] = []  # (sim, source id, other id)
    for start in range(0, len(src_rows), CHUNK):
        rows = src_rows[start:start + CHUNK]
        sims = mat[rows] @ mat.T
        sims[:, ~asset_mask] = -np.inf
        for r, row in enumerate(rows):
            sims[r, row] = -np.inf
            hit = np.nonzero(sims[r] >= th)[0]
            if len(hit) > 10:
                hit = hit[np.argpartition(-sims[r, hit], 9)[:10]]
            pairs += [(float(sims[r, j]), int(ids[row]), int(ids[j])) for j in hit]
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    made = 0
    per_source: dict[int, int] = {}
    two_hop: dict[int, set[int]] = {}
    seen_pairs: set[frozenset[int]] = set()
    for sim, sid, oid in pairs:
        if per_source.get(sid, 0) >= PER_SOURCE or frozenset((sid, oid)) in seen_pairs:
            continue
        e, other = g.by_id(sid), g.by_id(oid)
        if e is None or other is None or e.status == "rejected" or other.status == "rejected":
            continue
        if comm and comm.get(e.key) is not None and comm.get(e.key) == comm.get(other.key):
            continue
        if sid not in two_hop:
            two_hop[sid] = _two_hop(s, sid)
        if oid in two_hop[sid]:
            continue
        seen_pairs.add(frozenset((sid, oid)))
        a, b = sorted([e.key, other.key])
        if g.queue("synthesis", {"a": e.key, "b": other.key, "a_name": e.canonical_name[:300],
                                 "b_name": other.canonical_name[:300], "a_type": e.type, "b_type": other.type,
                                 "score": round(sim, 4)},
                   dedupe=f"synthesis:{a}|{b}", score=sim):
            made += 1
            per_source[sid] = per_source.get(sid, 0) + 1
        if made >= max_items:
            break
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
    last = s.get(KV, "last_synthesis")
    return {"since": since.isoformat(), "threshold": current_threshold(s),
            "threshold_default": get_settings().thresholds.synthesis_sim,
            "last_synthesis": (last.v or {}).get("at") if last else None, "pairs": pairs_out,
            "contradictions": contested,
            "contradictions_pending_review": [{"item_id": i.id, **i.payload} for i in pending_contra]}
