# SPDX-License-Identifier: Apache-2.0
"""Single-paper ingest: capture -> validate -> resolve IDs -> T0 -> T1 -> canonicalise -> edges ->
human decisions -> embeddings -> recall."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import rawstore
from ..config import get_settings
from ..db.models import Entity, Extraction, RawObject
from ..external import openalex
from ..external.ids import accession_format_ok, guess_database, normalize_repo
from ..external.verify import check_accession, check_repo
from ..rxf.loader import ValidationProblem, error_report, load_rxf
from ..rxf.schema import RxfDocument
from .graph import Graph
from .materialize import link_citations, materialize_openalex, materialize_rxf, promote_topics, work_key

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    ok: bool
    work_key: str | None = None
    work_id: int | None = None
    duplicate: bool = False
    problems: list[ValidationProblem] = field(default_factory=list)
    report: str | None = None
    suspect: list[str] = field(default_factory=list)
    new_entities: list[str] = field(default_factory=list)
    related: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "work_key": self.work_key, "work_id": self.work_id, "duplicate": self.duplicate,
            "problems": [p.__dict__ for p in self.problems], "report": self.report, "suspect": self.suspect,
            "new_entities": self.new_entities, "related": self.related,
        }


def _check_ids(doc: RxfDocument) -> tuple[dict[str, str], list[str]]:
    checks: dict[str, str] = {}
    for d in doc.assets.datasets:
        if not d.accession:
            continue
        acc = d.accession.strip()
        db = d.database or guess_database(acc)
        checks[acc] = check_accession(acc, db) if accession_format_ok(acc, d.database) else "bad_format"
    for m in doc.assets.methods:
        if m.repo:
            repo = normalize_repo(m.repo)
            if repo is None:
                checks[m.repo] = "bad_format"
            else:
                checks[repo] = check_repo(repo)
    suspect = [k for k, v in checks.items() if v in ("not_found", "bad_format")]
    return checks, suspect


def ingest_text(s: Session, text: str, filename: str = "inline.yaml", pdf: bytes | None = None,
                recall: bool = True) -> IngestResult:
    res = load_rxf(text)
    if not res.ok:
        return IngestResult(ok=False, problems=res.problems, report=error_report(filename, res.problems))
    doc = res.doc
    assert doc is not None
    data = text.encode("utf-8")
    sha = rawstore.sha256(data)
    raw = s.get(RawObject, sha)
    if raw is not None and raw.kind == "rxf":
        e = s.execute(select(Entity).where(Entity.key == raw.work_key)).scalar_one_or_none() if raw.work_key else None
        return IngestResult(ok=True, duplicate=True, work_key=raw.work_key, work_id=e.id if e else None)

    g = Graph(s)
    max_before = s.execute(select(func.coalesce(func.max(Entity.id), 0))).scalar_one()

    # resolve canonical work id (T0) and verify external ids before anything enters the graph
    oa = openalex.fetch_work(doi=doc.paper.doi) if doc.paper.doi else None
    checks, suspect = _check_ids(doc)
    meta = {"openalex_id": oa["id"] if oa else None, "checks": checks, "suspect": suspect,
            "filename": filename}
    wkey = work_key(meta["openalex_id"], doc.paper.doi, doc.paper.title)

    hashes = [rawstore.put(s, data, "rxf", filename, wkey)]
    if pdf:
        hashes.append(rawstore.put(s, pdf, "pdf", Path(filename).with_suffix(".pdf").name, wkey))

    if oa:
        have = s.execute(select(Extraction.id).where(Extraction.kind == "openalex", Extraction.is_current,
                                                     Extraction.work_key == wkey)).first()
        if not have:
            ex_oa = Extraction(kind="openalex", work_key=wkey, tier=0, model="openalex", prompt_version=None,
                               schema_version="openalex-v1", input_hashes=[], output=oa, meta={})
            s.add(ex_oa)
            s.flush()
            materialize_openalex(g, ex_oa)

    ex = Extraction(kind="rxf", work_key=wkey, tier=2 if pdf and doc.depth == "deep" else 1,
                    model="chat-export", prompt_version=doc.prompt_version,
                    schema_version=f"rxf-v{doc.rxf_version}", input_hashes=hashes,
                    output=doc.model_dump(mode="json"), meta=meta)
    s.add(ex)
    s.flush()
    out = materialize_rxf(g, ex)
    if suspect:
        g.update_attrs(out.work, suspect_ids=sorted(set(out.work.attrs.get("suspect_ids", [])) | set(suspect)))
    link_citations(g, only_work_ids={out.work.id})
    # Human decisions persist on the incremental path without re-applying them: merges act through
    # key redirects and rejected/confirmed edges keep their status in upsert_edge.
    promote_topics(g)
    s.flush()
    out.work = g.by_key(out.work.key) or out.work
    new = [e.key for e in s.execute(select(Entity).where(Entity.id > max_before)).scalars()]
    result = IngestResult(ok=True, work_key=out.work.key, work_id=out.work.id, suspect=suspect, new_entities=new)
    if recall:
        from ..services.recall import related_to_work

        result.related = related_to_work(s, out.work.id)
    return result


def ingest_path(s: Session, path: Path, move: bool = True) -> IngestResult:
    """Ingest an inbox file (and a same-named PDF if present); move to done/ or error/."""
    text = path.read_text(encoding="utf-8-sig")
    pdf_path = path.with_suffix(".pdf")
    pdf = pdf_path.read_bytes() if pdf_path.exists() else None
    result = ingest_text(s, text, path.name, pdf)
    if move:
        inbox = get_settings().inbox
        target = inbox / ("done" if result.ok else "error")
        target.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(_unique(target / path.name)))
        if pdf is not None:
            shutil.move(str(pdf_path), str(_unique(target / pdf_path.name)))
        if not result.ok and result.report:
            (target / (path.name + ".error.txt")).write_text(result.report, encoding="utf-8")
    return result


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    i = 1
    while (q := p.with_name(f"{p.stem}.{i}{p.suffix}")).exists():
        i += 1
    return q
