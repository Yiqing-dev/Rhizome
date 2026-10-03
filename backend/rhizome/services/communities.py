# SPDX-License-Identifier: Apache-2.0
"""Community detection for the nightly batch. Uses graspologic's Leiden (MIT) when installed,
otherwise a deterministic label-propagation fallback. (igraph/leidenalg are GPL: not used.)"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import KV, Edge, Entity

log = logging.getLogger(__name__)
HUB_TYPES = ("organism", "modality")  # near-universal hubs would glue everything together


def _graph(s: Session) -> tuple[dict[int, str], list[tuple[int, int]]]:
    keys = dict(s.execute(select(Entity.id, Entity.key).where(Entity.type.notin_(HUB_TYPES))).all())
    edges = [(a, b) for a, b in s.execute(select(Edge.src, Edge.dst).where(Edge.status != "rejected")).all()
             if a in keys and b in keys]
    return keys, edges


def _label_propagation(nodes: list[int], edges: list[tuple[int, int]], iters: int = 30) -> dict[int, int]:
    adj: dict[int, list[int]] = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    label = {n: n for n in nodes}
    for _ in range(iters):
        changed = False
        for n in nodes:
            if not adj[n]:
                continue
            c = Counter(label[m] for m in adj[n])
            top = max(c.values())
            best = min(lbl for lbl, v in c.items() if v == top)
            if best != label[n]:
                label[n] = best
                changed = True
        if not changed:
            break
    return label


def detect(s: Session) -> dict[str, int]:
    keys, edges = _graph(s)
    nodes = sorted(keys)
    try:
        from graspologic.partition import leiden

        part = leiden([(str(a), str(b), 1.0) for a, b in edges], random_seed=42) if edges else {}
        label = {n: int(part.get(str(n), -n)) for n in nodes}
    except ImportError:
        label = _label_propagation(nodes, edges)
    # renumber by size
    sizes = Counter(label.values())
    order = {lbl: i for i, (lbl, _) in enumerate(sizes.most_common())}
    result = {keys[n]: order[label[n]] for n in nodes}
    row = s.get(KV, "communities")
    if row is None:
        s.add(KV(k="communities", v=result))
    else:
        row.v = result
    return result
