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

from ..db.models import KV, Edge, Embedding, Entity, EntityAlias, Extraction, RawObject, ReviewItem, Work
from ..db.session import backup_database, has_fts, recent_backup
from ..config import get_settings
from .decisions import apply_all
from .graph import VECTORS, Graph, set_index_model
from .materialize import link_citations, materialize, promote_topics

log = logging.getLogger(__name__)

REGENERATED_REVIEW_KINDS = ("merge", "topic_relation", "contradiction")


PREWARM_BATCH = 256


def _known_taxa(s: Session) -> dict[str, str]:
    """Organism alias -> taxid as resolved so far (rebuild must not look them up online again)."""
    from ..text import norm

    out: dict[str, str] = {}
    rows = s.execute(select(EntityAlias.alias, Entity.external_id).join(Entity, Entity.id == EntityAlias.entity_id)
                     .where(Entity.type == "organism", Entity.external_id.like("taxon:%"))).all()
    for alias, ext in rows:
        out[norm(alias)] = ext.split(":", 1)[1]
    return out


def _restamp(s: Session, ex: Extraction, work: Entity) -> None:
    """L0/L1 rows follow the paper they materialise into *now*: a merge moved them to the merge
    target, and after that merge is revoked they must come back to their own paper."""
    if work is None or ex.work_key == work.key:
        return
    ex.work_key = work.key
    for h in ex.input_hashes or []:
        obj = s.get(RawObject, h)
        if obj is not None:
            obj.work_key = work.key


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


class RebuildAborted(RuntimeError):
    """Stored exports that would not replay: nothing was deleted. ``failures`` lists them."""

    def __init__(self, failures: list[dict]):
        self.failures = failures
        super().__init__(f"{len(failures)} stored export(s) would not replay: "
                         + ", ".join(f"#{f['extraction_id']} ({f['work_key']}): {f['error'][:80]}" for f in failures[:5]))


def preflight(s: Session) -> list[dict]:
    """Validate every current RXF row with the models of its stored version *before* anything is
    deleted: a row the current code cannot read must stop the rebuild (or be listed, with
    ``force``), never vanish silently from the graph."""
    from ..rxf.schema import parse_stored

    out = []
    for ex in s.execute(select(Extraction).where(Extraction.is_current, Extraction.kind == "rxf")
                        .order_by(Extraction.id)).scalars():
        try:
            parse_stored(ex.output, ex.schema_version)
        except Exception as e:  # noqa: BLE001 - any validation error
            out.append({"extraction_id": ex.id, "work_key": ex.work_key, "error": " ".join(str(e).split())[:300]})
    return out


def rebuild(s: Session, backup: bool = True, force: bool = False) -> dict:
    t0 = time.time()
    warnings: list[str] = []
    bad = preflight(s)
    if bad and not force:
        raise RebuildAborted(bad)
    failed: list[dict] = list(bad)
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
    from ..config import adopt_file_embedder
    from ..ml import get_embedder, reset_models

    adopt_file_embedder()
    reset_models()
    prewarmed = _prewarm_embeddings(s)
    # ids are handles (UI URLs, CLI, MCP, review items): remember them so the same key gets the same
    # id back, and a new entity never inherits the id of one that is gone
    id_plan = dict(s.execute(select(Entity.key, Entity.id)).all())
    taxa_fallback = _known_taxa(s)
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
    g.taxa_fallback = taxa_fallback
    n = 0
    current = s.execute(select(Extraction).where(Extraction.is_current).order_by(Extraction.id)).scalars().all()
    # pass 1: paper-level extractions; pass 2 (after decisions, which may create topics): retro tags
    for phase in (("rxf", "openalex"), ("retro_tag",)):
        for ex in (x for x in current if x.kind in phase):
            try:
                out = materialize(g, ex)
                n += 1
                if ex.kind in ("rxf", "openalex"):
                    _restamp(s, ex, out.work if ex.kind == "rxf" else out)
            except Exception as e:  # keep going; report at the end
                log.exception("extraction %s failed to materialise: %s", ex.id, e)
                if not any(f["extraction_id"] == ex.id for f in failed):
                    failed.append({"extraction_id": ex.id, "work_key": ex.work_key, "error": str(e)[:300]})
        if phase[0] == "rxf":
            cites = link_citations(g)
            skipped: list[dict] = []
            applied = apply_all(g, skipped)
    if any(x.kind == "retro_tag" for x in current):
        skipped = []
        applied = apply_all(g, skipped)  # decisions about retro-tag edges (e.g. rejections) win again
    if failed:
        warnings.append(f"{len(failed)} stored export(s) did not replay and are missing from the graph: "
                        + ", ".join(f"#{f['extraction_id']}" for f in failed))
    if skipped:
        warnings.append(f"{len(skipped)} decision(s) could not be replayed: "
                        + ", ".join(f"#{x['id']} {x['op']}" for x in skipped))
    promoted = promote_topics(g)
    s.flush()
    from .materialize import reconcile_cards

    cards = reconcile_cards(g)
    from .graph import prune_vector_cache

    pruned = prune_vector_cache(s)
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
            "seconds": round(time.time() - t0, 2), "prewarmed": prewarmed, "cards": cards, "decisions_skipped": skipped,
            "vector_cache_pruned": pruned, "failed": failed,
            "warnings": warnings}
