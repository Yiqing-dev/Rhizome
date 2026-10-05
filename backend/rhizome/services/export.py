# SPDX-License-Identifier: Apache-2.0
"""`rhz export`: the library as JSONL files anyone can read without Rhizome. Rows carry entity
keys, not row ids, so the files stay meaningful across rebuilds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..db.models import Edge, Entity, EntityAlias, HumanDecision, RawObject, ReviewCard, Work


def _write(path: Path, rows) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            n += 1
    return n


def export_library(s: Session, out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    keys = dict(s.execute(select(Entity.id, Entity.key)).all())
    works = {w.entity_id: w for w in s.execute(select(Work)).scalars()}
    aliases: dict[int, list[dict[str, str]]] = {}
    for a in s.execute(select(EntityAlias).order_by(EntityAlias.id)).scalars():
        aliases.setdefault(a.entity_id, []).append({"alias": a.alias, "lang": a.lang, "source": a.source})

    def entity_rows():
        for e in s.execute(select(Entity).order_by(Entity.id)).scalars():
            row = {"key": e.key, "type": e.type, "name": e.canonical_name, "status": e.status,
                   "external_id": e.external_id, "attrs": e.attrs or {}, "aliases": aliases.get(e.id, []),
                   "created_at": e.created_at}
            w = works.get(e.id)
            if w is not None:
                row["work"] = {"dois": w.dois, "openalex_id": w.openalex_id, "year": w.year, "tier": w.tier}
            yield row

    def edge_rows():
        for ed in s.execute(select(Edge).order_by(Edge.id)).scalars():
            if ed.src in keys and ed.dst in keys:
                yield {"src": keys[ed.src], "dst": keys[ed.dst], "type": ed.type, "status": ed.status,
                       "confidence": ed.confidence, "evidence": ed.evidence, "attrs": ed.attrs or {},
                       "extraction_id": ed.extraction_id, "created_at": ed.created_at}

    def decision_rows():
        for d in s.execute(select(HumanDecision).order_by(HumanDecision.id)).scalars():
            yield {"id": d.id, "op": d.op, "payload": d.payload, "created_at": d.created_at, "revoked_at": d.revoked_at}

    def card_rows():
        for c in s.execute(select(ReviewCard).order_by(ReviewCard.id)).scalars():
            yield {"id": c.id, "entity_key": c.entity_key, "q": c.q, "a": c.a, "origin": c.origin, "priority": c.priority,
                   "due": c.due, "introduced_at": c.introduced_at, "suspended": c.suspended, "state": c.state}

    def pdf_rows():
        from .. import rawstore

        for r in s.execute(select(RawObject).where(RawObject.kind == "pdf")).scalars():
            yield {"sha256": r.sha256, "work_key": r.work_key, "file": str(rawstore.path_for(r.sha256, "pdf")), "name": r.uri}

    counts = {"entities": _write(out / "entities.jsonl", entity_rows()), "edges": _write(out / "edges.jsonl", edge_rows()),
              "decisions": _write(out / "decisions.jsonl", decision_rows()), "cards": _write(out / "cards.jsonl", card_rows()),
              "pdfs": _write(out / "pdfs.jsonl", pdf_rows())}
    (out / "README.txt").write_text(
        f"Rhizome {__version__} export. One JSON object per line. entities.jsonl: every node with its aliases "
        "(works carry dois / openalex_id / year); edges.jsonl: src and dst are entity keys; decisions.jsonl: your "
        "corrections; cards.jsonl: review cards with their scheduling state; pdfs.jsonl: where the PDFs are in raw/.\n",
        "utf-8")
    return counts
