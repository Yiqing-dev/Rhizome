# SPDX-License-Identifier: Apache-2.0
"""`rhz data <accession>`: how to download a dataset, and which papers it is linked to."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Edge, Entity
from ..external.ids import effective_database, normalize_zenodo
from ..i18n import _
from ..pipeline.graph import Graph
from .search import summarize


_RUN = re.compile(r"^(SRR|ERR|DRR|SRX|ERX|DRX)\d+$")
_PROJECT = re.compile(r"^(PRJ[NED][AB]|SRP|ERP|DRP|SRS|ERS|DRS|SAMEA|SRA|ERA|DRA)\d+$")


def download_hint(accession: str, database: str | None) -> str:
    """How to get the data: a run is fetched directly, a study/project needs its run list first
    (ENA's file report lists every INSDC run with FASTQ links), a GEO series has processed files
    under the series' FTP folder."""
    acc = accession.strip()
    if "zenodo" in acc.lower() or database == "Zenodo":
        return _("dl.Zenodo", acc=normalize_zenodo(acc))
    db = effective_database(acc, database) or "other"
    if _PROJECT.match(acc):
        return _("dl.SRA_project", acc=acc)
    if _RUN.match(acc):
        return _("dl.SRA_run", acc=acc)
    if db == "GEO" and acc.startswith("GSE"):
        return _("dl.GEO_series", acc=acc, bucket=f"GSE{acc[3:-3]}nnn")  # GSE999001 -> GSE999nnn, GSE12 -> GSEnnn
    if db not in ("GEO", "SRA", "ENA", "ArrayExpress", "CNGB", "GSA"):
        db = "other"
    return _(f"dl.{db}", acc=acc)


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
