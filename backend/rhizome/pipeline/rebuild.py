# SPDX-License-Identifier: Apache-2.0
"""Recompute: rebuild L2/L3 from current L1 rows, then apply human decisions last.

Triggered when local models, mapping rules or the schema version change. Chat exports are never
regenerated (the conversation cannot be re-run); they are re-parsed with the current rules.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ..db.models import Edge, Embedding, Entity, EntityAlias, Extraction, ReviewItem, Work
from ..db.session import backup_database, has_fts
from ..config import get_settings
from .decisions import apply_all
from .graph import VECTORS, Graph
from .materialize import link_citations, materialize, promote_topics

log = logging.getLogger(__name__)

REGENERATED_REVIEW_KINDS = ("merge", "topic_relation", "contradiction")


def rebuild(s: Session, backup: bool = True) -> dict:
    t0 = time.time()
    if backup:
        s.commit()
        backup_database(get_settings(), tag="pre-rebuild")
    for model in (Edge, Embedding, EntityAlias, Work, Entity):
        s.execute(delete(model))
    if has_fts(s):
        s.execute(text("delete from entity_fts"))
    s.execute(delete(ReviewItem).where(ReviewItem.status == "pending",
                                       ReviewItem.kind.in_(REGENERATED_REVIEW_KINDS)))
    s.flush()
    VECTORS.clear()
    g = Graph(s)
    n = 0
    current = s.execute(select(Extraction).where(Extraction.is_current).order_by(Extraction.id)).scalars().all()
    # pass 1: paper-level extractions; pass 2 (after decisions, which may create topics): retro tags
    for phase in (("rxf", "openalex"), ("retro_tag",)):
        for ex in (x for x in current if x.kind in phase):
            try:
                materialize(g, ex)
                n += 1
            except Exception as e:  # keep going; report at the end
                log.exception("extraction %s failed to materialise: %s", ex.id, e)
        if phase[0] == "rxf":
            cites = link_citations(g)
            applied = apply_all(g)
    if any(x.kind == "retro_tag" for x in current):
        applied = apply_all(g)  # decisions about retro-tag edges (e.g. rejections) win again
    promoted = promote_topics(g)
    s.flush()
    return {"extractions": n, "cites": cites, "decisions_applied": applied, "topics_promoted": promoted,
            "entities": s.query(Entity).count(), "edges": s.query(Edge).count(),
            "seconds": round(time.time() - t0, 2)}
