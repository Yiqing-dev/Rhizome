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
from ..external.ids import accession_format_ok, guess_database, normalize_doi, normalize_repo
from ..external.verify import check_accession, check_repo
from ..rxf.loader import ValidationProblem, load_rxf, report_for
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
    repairs: list[str] = field(default_factory=list)  # known-drift fixes applied on request
    repairable: list[str] = field(default_factory=list)  # fixes that would make a failed file valid

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "work_key": self.work_key, "work_id": self.work_id, "duplicate": self.duplicate,
            "problems": [p.__dict__ for p in self.problems], "report": self.report, "suspect": self.suspect,
            "new_entities": self.new_entities, "related": self.related,
            "repairs": self.repairs, "repairable": self.repairable,
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
                recall: bool = True, repair: bool = False) -> IngestResult:
    """``repair`` applies the known-drift fixes (rxf/repair.py): L0 keeps the original bytes, the L1
    row holds the repaired document and records the fixes in ``meta.repairs``."""
    res = load_rxf(text, repair=repair)
    if not res.ok:
        return IngestResult(ok=False, problems=res.problems, report=report_for(filename, res),
                            repairable=res.repairable)
    doc = res.doc
    assert doc is not None
    data = text.encode("utf-8")
    sha = rawstore.sha256(data)
    g = Graph(s)
    raw = s.get(RawObject, sha)
    if raw is not None and raw.kind == "rxf":
        e = g.by_key(raw.work_key) if raw.work_key else None  # follows human merges
        return IngestResult(ok=True, duplicate=True, work_key=e.key if e else raw.work_key,
                            work_id=e.id if e else None)

    max_before = s.execute(select(func.coalesce(func.max(Entity.id), 0))).scalar_one()

    # resolve canonical work id (T0) and verify external ids before anything enters the graph
    oa = openalex.fetch_work(doi=doc.paper.doi) if doc.paper.doi else None
    checks, suspect = _check_ids(doc)
    meta = {"openalex_id": oa["id"] if oa else None, "checks": checks, "suspect": suspect,
            "filename": filename}
    if res.repairs:
        meta["repairs"] = res.repairs
    wkey = work_key(meta["openalex_id"], doc.paper.doi, doc.paper.title)

    hashes = [rawstore.put(s, data, "rxf", filename, wkey)]
    if pdf:
        hashes.append(rawstore.put(s, pdf, "pdf", Path(filename).with_suffix(".pdf").name, wkey))

    ex_oa = None
    if oa:
        # the work may already exist under its DOI key (ingested offline earlier) or the OpenAlex key
        known = {wkey, f"work:openalex:{oa['id']}"} | ({f"work:doi:{normalize_doi(doc.paper.doi)}"} if doc.paper.doi else set())
        existing_work = next((g.by_key(k) for k in known if g.by_key(k) is not None), None)
        if existing_work is not None:
            known.add(existing_work.key)
        have = s.execute(select(Extraction.id).where(Extraction.kind == "openalex", Extraction.is_current,
                                                     Extraction.work_key.in_(known))).first()
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
    # resolve_work may have matched an existing node (other DOI, title, OpenAlex id): L0/L1 rows
    # must carry the key the card looks them up by
    if out.work.key != wkey:
        ex.work_key = out.work.key
        if ex_oa is not None:
            ex_oa.work_key = out.work.key
        for h in hashes:
            obj = s.get(RawObject, h)
            if obj is not None:
                obj.work_key = out.work.key
    if suspect:
        g.update_attrs(out.work, suspect_ids=sorted(set(out.work.attrs.get("suspect_ids", [])) | set(suspect)))
    link_citations(g, only_work_ids={out.work.id})
    # Human decisions persist on the incremental path without re-applying them: merges act through
    # key redirects and rejected/confirmed edges keep their status in upsert_edge.
    promote_topics(g)
    s.flush()
    out.work = g.by_key(out.work.key) or out.work
    new = [e.key for e in s.execute(select(Entity).where(Entity.id > max_before)).scalars()]
    result = IngestResult(ok=True, work_key=out.work.key, work_id=out.work.id, suspect=suspect, new_entities=new,
                          repairs=res.repairs)
    if recall:
        from ..services.recall import related_to_work

        result.related = related_to_work(s, out.work.id)
    return result


def read_inbox_file(path: Path) -> tuple[str, bytes | None]:
    """RXF text plus the same-named PDF, if present."""
    pdf_path = path.with_suffix(".pdf")
    return path.read_text(encoding="utf-8-sig"), (pdf_path.read_bytes() if pdf_path.exists() else None)


def ingest_path(s: Session, path: Path, repair: bool = False) -> IngestResult:
    """Ingest an inbox file (and a same-named PDF). Does not move anything: the caller commits first."""
    text, pdf = read_inbox_file(path)
    return ingest_text(s, text, path.name, pdf, repair=repair)


def file_done(path: Path, result: IngestResult | None, error: str | None = None) -> Path:
    """After the transaction committed: move the file (and PDF) to done/ or error/, with a report."""
    inbox = get_settings().inbox
    ok = result is not None and result.ok and error is None
    target = inbox / ("done" if ok else "error")
    target.mkdir(parents=True, exist_ok=True)
    pdf_path = path.with_suffix(".pdf")
    if path.parent.resolve() == target.resolve():  # retried from error/ and failed again: stays put
        dest = path
    else:
        dest = _unique(target / path.name)
        shutil.move(str(path), str(dest))
        if pdf_path.exists():
            shutil.move(str(pdf_path), str(_unique(target / pdf_path.name)))
    report = error if error else (result.report if result is not None and not result.ok else None)
    stale = path.with_name(path.name + ".error.txt")  # retried from error/: the old report is obsolete
    if stale.exists() and stale != target / (dest.name + ".error.txt"):
        stale.unlink()
    if report:
        (target / (dest.name + ".error.txt")).write_text(report, encoding="utf-8")
    return dest


def ingest_file(path: Path, repair: bool = False) -> IngestResult:
    """Own transaction per file; the move happens only after the commit succeeded."""
    import traceback

    from ..db.session import session_scope

    from ..ml import ModelUnavailable

    try:
        with session_scope() as s:
            result = ingest_path(s, path, repair=repair)
    except ModelUnavailable as e:  # not the file's fault: leave it in the inbox for after the fix
        log.error("inbox: %s left in place: %s", path.name, e)
        return IngestResult(ok=False, report=str(e))
    except Exception as e:  # keep the inbox flowing; the report tells the user what happened
        log.exception("ingest failed for %s", path)
        file_done(path, None, error=f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=8)}")
        return IngestResult(ok=False, report=str(e))
    file_done(path, result)
    return result


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    i = 1
    while (q := p.with_name(f"{p.stem}.{i}{p.suffix}")).exists():
        i += 1
    return q
