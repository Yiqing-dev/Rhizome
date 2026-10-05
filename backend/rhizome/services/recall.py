# SPDX-License-Identifier: Apache-2.0
"""Proactive recall: "you forgot it, but it is useful now".

Ranking signal shared with synthesis and review: relevance x forgetting, where forgetting grows
with the time since the user last opened or reviewed the asset.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import AccessLog, Edge, Entity, utcnow
from ..ml import get_reranker
from ..pipeline.graph import Graph, embed_texts, entity_text, knn
from .search import Filters, search, source_works

ASSET_TYPES = ("dataset", "method", "idea", "claim")
TAU_DAYS = 30.0


def touch(s: Session, key: str) -> None:
    if s.info.get("read_only"):
        return
    row = s.get(AccessLog, key)
    if row is None:
        s.add(AccessLog(entity_key=key, last_seen_at=utcnow(), count=1))
    else:
        row.last_seen_at = utcnow()
        row.count += 1


def forgetting(s: Session, entities: list[Entity], now: datetime | None = None) -> dict[int, float]:
    now = now or utcnow()
    seen = {r.entity_key: r.last_seen_at for r in
            s.execute(select(AccessLog).where(AccessLog.entity_key.in_([e.key for e in entities]))).scalars()}
    out = {}
    for e in entities:
        last = seen.get(e.key) or e.created_at or now
        days = max(0.0, (now - last).total_seconds() / 86400)
        out[e.id] = 1.0 - math.exp(-days / TAU_DAYS)
    return out


def recall(s: Session, context: str, limit: int | None = None, types: tuple[str, ...] = ASSET_TYPES + ("work",),
           min_relevance: float | None = None) -> list[dict[str, Any]]:
    th = get_settings().thresholds
    limit = limit or th.recall_limit
    min_rel = th.recall_min if min_relevance is None else min_relevance
    if not context or not context.strip():
        return []  # an empty context is not a query (search would fall back to browsing)
    hits = search(s, context[:4000], Filters(types=types, include_candidates=False), limit=50)
    if not hits:
        return []
    ents = {e.id: e for e in s.execute(select(Entity).where(Entity.id.in_([h["id"] for h in hits]))).scalars()}
    forget = forgetting(s, list(ents.values()))
    out = []
    for h in hits:
        rel = h.get("relevance") or 0.0
        if rel < min_rel:
            continue
        weight = 2.0 if h.get("origin") == "user" else 1.0
        h = dict(h, relevance=round(rel, 4), forgetting=round(forget.get(h["id"], 0.0), 4),
                 score=round(rel * (0.5 + 0.5 * forget.get(h["id"], 0.0)) * weight, 4))
        out.append(h)
    out.sort(key=lambda h: -h["score"])
    return out[:limit]


# ---- recall for code files (HPC) ----------------------------------------------------

_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w., ]+))", re.M)
_R_LIB = re.compile(r"(?:library|require)\(\s*['\"]?([\w.]+)['\"]?\s*\)")
_COMMENT = re.compile(r"(?:^|\s)(?:#|//|%)\s?(.+)$", re.M)


_SCHEDULER = re.compile(r"^\s*#\s*(?:SBATCH|PBS|\$|BSUB)\b.*$", re.M | re.I)


def _notebook_text(source: str) -> str | None:
    """A Jupyter notebook (.ipynb JSON): its code and markdown cells as plain text."""
    import json

    try:
        nb = json.loads(source)
    except ValueError:
        return None
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        return None
    parts = []
    for c in nb["cells"]:
        src = c.get("source", "") if isinstance(c, dict) else ""
        text = "".join(src) if isinstance(src, list) else str(src)
        if c.get("cell_type") == "markdown":
            text = "\n".join("# " + ln for ln in text.splitlines())  # prose counts like comments
        parts.append(text)
    return "\n".join(parts)


def context_from_code(source: str) -> str:
    nb = _notebook_text(source) if source.lstrip().startswith("{") else None
    if nb is not None:
        source = nb
    source = _SCHEDULER.sub("", source)  # #SBATCH / #PBS directives are not about the analysis
    libs: list[str] = []
    for a, b in _PY_IMPORT.findall(source):
        libs += [x.strip().split(" as ")[0].split(".")[0] for x in (a or b).split(",") if x.strip()]
    libs += _R_LIB.findall(source)
    comments = [c.strip() for c in _COMMENT.findall(source) if len(c.strip()) > 3 and not c.startswith("!")]
    std = {"os", "sys", "re", "json", "math", "time", "typing", "pathlib", "collections", "itertools",
           "functools", "subprocess", "argparse", "logging", "datetime", "random", "glob", "shutil"}
    libs = [lib for lib in dict.fromkeys(libs) if lib not in std]
    out = (" ".join(libs) + "\n" + "\n".join(comments[:80])).strip()
    if not out:  # no imports or comments: the code itself is the best context we have
        out = source.strip()[:4000]
    return out


# ---- recall on ingest: which papers you read relate to the new one, and along which dimension

def related_to_work(s: Session, work_id: int, limit: int = 5) -> list[dict[str, Any]]:
    g = Graph(s)
    work = g.by_id(work_id)
    if work is None:
        return []
    mine = list(s.execute(select(Entity).join(Edge, Edge.dst == Entity.id)
                          .where(Edge.src == work_id, Entity.type.in_(ASSET_TYPES))).scalars())
    own_ids = {work_id, *[e.id for e in mine]}
    scores: dict[int, float] = defaultdict(float)
    dims: dict[int, list[dict[str, str]]] = defaultdict(list)

    # semantic: nearest assets from other works
    rr = get_reranker()
    for e in [work, *mine]:
        qv = embed_texts(s, [entity_text(e)])[0]
        for nid, sim in knn(s, qv, types=ASSET_TYPES + ("work",), k=8, exclude=own_ids):
            if sim < 0.2:
                continue
            other = g.by_id(nid)
            if other is None:
                continue
            rel = rr.score(entity_text(e), [entity_text(other)])[0]
            if rel < 0.3:
                continue
            owners = [other.id] if other.type == "work" else [w["work_id"] for w in source_works(s, [nid])[nid]]
            for w in owners:
                if w == work_id:
                    continue
                scores[w] += rel
                dims[w].append({"dimension": other.type, "mine": e.canonical_name[:200],
                                "theirs": other.canonical_name[:200]})

    # structural: shared topics / organisms / datasets / methods
    shared_rows = s.execute(
        select(Edge.src, Edge.type, Entity.type, Entity.canonical_name)
        .join(Entity, Entity.id == Edge.dst)
        .where(Edge.dst.in_(select(Edge.dst).where(Edge.src == work_id)), Edge.src != work_id,
               Edge.status != "rejected",
               Entity.type.in_(("topic", "dataset", "method")))
    ).all()
    for src, etype, dtype, dname in shared_rows:
        src_ent = g.by_id(src)
        if src_ent is None or src_ent.type != "work":
            continue
        scores[src] += 0.5
        dims[src].append({"dimension": dtype, "mine": dname, "theirs": dname, "edge": etype})

    ranked = sorted(scores, key=lambda w: -scores[w])[:limit]
    out = []
    for w in ranked:
        e = g.by_id(w)
        uniq = list({(d["dimension"], d["mine"], d["theirs"]): d for d in dims[w]}.values())[:6]
        out.append({"work_id": w, "key": e.key, "title": e.canonical_name, "score": round(scores[w], 3),
                    "via": uniq})
    return out
