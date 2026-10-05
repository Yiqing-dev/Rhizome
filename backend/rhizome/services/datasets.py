# SPDX-License-Identifier: Apache-2.0
"""`rhz data <accession>`: how to download a dataset, and which papers it is linked to."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Edge, Entity
from ..external.ids import guess_database
from ..i18n import _
from ..pipeline.graph import Graph
from .search import summarize


def download_hint(accession: str, database: str | None) -> str:
    db = database or guess_database(accession) or "other"
    if db not in ("GEO", "SRA", "ENA", "ArrayExpress", "CNGB"):
        db = "other"
    return _(f"dl.{db}", acc=accession)


def dataset_info(s: Session, accession: str) -> dict[str, Any] | None:
    g = Graph(s)
    e = g.by_key(f"dataset:{accession}") or g.by_alias("dataset", accession)
    if e is None or e.status == "rejected":
        return None
    papers = s.execute(select(Edge.type, Edge.evidence, Entity).join(Entity, Entity.id == Edge.src)
                       .where(Edge.dst == e.id, Entity.type == "work", Edge.status != "rejected")).all()
    a = e.attrs or {}
    return {
        **summarize(e), "attrs": a,
        "download": download_hint(e.external_id or accession, a.get("database")) if e.external_id else None,
        "papers": [dict(summarize(w), edge=t, evidence=ev) for t, ev, w in papers],
    }
