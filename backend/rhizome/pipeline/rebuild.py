# SPDX-License-Identifier: Apache-2.0
"""Recompute: rebuild L2/L3 from current L1 rows, then apply human decisions last.

Triggered when local models, mapping rules or the schema version change. Chat exports are never
regenerated (the conversation cannot be re-run); they are re-parsed with the current rules.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ..db.models import KV, Edge, Embedding, Entity, EntityAlias, Extraction, ReviewItem, Work
from ..db.session import backup_database, has_fts, recent_backup
from ..config import get_settings
from .decisions import apply_all
from .graph import VECTORS, Graph, set_index_model
from .materialize import link_citations, materialize, promote_topics

log = logging.getLogger(__name__)

REGENERATED_REVIEW_KINDS = ("merge", "topic_relation", "contradiction")


PREWARM_BATCH = 256


def _prewarm_embeddings(s: Session) -> int:
    """Embed the current entities' texts in committed batches *before* the destructive part.
    After a model switch this is the slow step (hours on CPU for a large library): done here, it
    holds the write lock only per batch, other processes keep working in between, and a rebuild
    that is interrupted resumes from the cache instead of from zero. Rebuilt entities mostly have
    the same texts, so the locked phase below finds them in the cache."""
    from .graph import embed_texts, entity_text

    rows = s.execute(select(Entity).where(Entity.type.notin_(("organism", "modality"))).order_by(Entity.id)).scalars().all()
    texts = list(dict.fromkeys(entity_text(e) for e in rows))
    for i in range(0, len(texts), PREWARM_BATCH):
        embed_texts(s, texts[i:i + PREWARM_BATCH])
        s.commit()
    return len(texts)


def rebuild(s: Session, backup: bool = True) -> dict:
    t0 = time.time()
    warnings: list[str] = []
    if backup:
        s.commit()
        st = get_settings()
        # one safety copy per burst of rebuilds (revoking several decisions queues several); a
        # failed copy (disk full, backup folder offline) must not block the rebuild itself
        if not recent_backup(st, "pre-rebuild", within=600):
            try:
                backup_database(st, tag="pre-rebuild")
            except Exception as e:  # noqa: BLE001
                log.warning("pre-rebuild backup failed: %s", e)
                warnings.append(f"pre-rebuild backup failed: {e}")
    # adopt the embedder in settings.json now (another process may have switched it): a rebuild is
    # what re-indexes the library for it
    from ..config import reload_settings
    from ..ml import get_embedder, reset_models

    reload_settings()
    reset_models()
    prewarmed = _prewarm_embeddings(s)
    # ids are handles (UI URLs, CLI, MCP, review items): remember them so the same key gets the same
    # id back, and a new entity never inherits the id of one that is gone
    id_plan = dict(s.execute(select(Entity.key, Entity.id)).all())
    item_plan = dict(s.execute(select(ReviewItem.dedupe_key, ReviewItem.id).where(
        ReviewItem.status == "pending", ReviewItem.kind.in_(REGENERATED_REVIEW_KINDS))).all())
    high = s.get(KV, "id_high_water")
    hv = dict(high.v) if high and high.v else {}
    next_id = max(s.execute(select(func.coalesce(func.max(Entity.id), 0))).scalar_one(), hv.get("entity", 0)) + 1
    next_item = max(s.execute(select(func.coalesce(func.max(ReviewItem.id), 0))).scalar_one(),
                    hv.get("review_item", 0)) + 1
    for model in (Edge, Embedding, EntityAlias, Work, Entity):
        s.execute(delete(model))
    if has_fts(s):
        s.execute(text("delete from entity_fts"))
    s.execute(delete(ReviewItem).where(ReviewItem.status == "pending",
                                       ReviewItem.kind.in_(REGENERATED_REVIEW_KINDS)))
    s.flush()
    VECTORS.clear()
    set_index_model(s, get_embedder().name)  # same transaction as the vectors it describes
    s.info["index_model_checked"] = True
    g = Graph(s)
    g.id_plan, g.next_id, g.item_plan, g.next_item_id = id_plan, next_id, item_plan, next_item
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
            skipped: list[dict] = []
            applied = apply_all(g, skipped)
    if any(x.kind == "retro_tag" for x in current):
        skipped = []
        applied = apply_all(g, skipped)  # decisions about retro-tag edges (e.g. rejections) win again
    if skipped:
        warnings.append(f"{len(skipped)} decision(s) could not be replayed: "
                        + ", ".join(f"#{x['id']} {x['op']}" for x in skipped))
    promoted = promote_topics(g)
    s.flush()
    hv = {"entity": max(g.next_id - 1, s.execute(select(func.coalesce(func.max(Entity.id), 0))).scalar_one()),
          "review_item": max(g.next_item_id - 1,
                             s.execute(select(func.coalesce(func.max(ReviewItem.id), 0))).scalar_one())}
    if high is None:
        s.add(KV(k="id_high_water", v=hv))
    else:
        high.v = hv
    g.id_plan = g.item_plan = None
    return {"extractions": n, "cites": cites, "decisions_applied": applied, "topics_promoted": promoted,
            "entities": s.query(Entity).count(), "edges": s.query(Edge).count(),
            "seconds": round(time.time() - t0, 2), "prewarmed": prewarmed, "decisions_skipped": skipped,
            "warnings": warnings}
