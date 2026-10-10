# SPDX-License-Identifier: Apache-2.0
"""Single-paper ingest: capture -> validate -> resolve IDs -> T0 -> T1 -> canonicalise -> edges ->
human decisions -> embeddings -> recall."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .. import rawstore
from ..config import get_settings
from ..db.models import Entity, Extraction, RawObject, Work, utcnow
from ..external import openalex
from ..external.ids import accession_format_ok, is_url, normalize_doi, normalize_repo
from ..external.verify import check_accession, check_repo, taxonomy_lookup
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
    existing_exports: list[int] = field(default_factory=list)  # earlier current exports of this paper
    replaced: list[int] = field(default_factory=list)  # exports retracted because replace=True
    rebuild_job: int | None = None
    repairable: list[str] = field(default_factory=list)  # fixes that would make a failed file valid
    pdf_attached: bool | None = None  # a re-sent export with its PDF: attached (True) or already had one (False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "work_key": self.work_key, "work_id": self.work_id, "duplicate": self.duplicate,
            "problems": [p.__dict__ for p in self.problems], "report": self.report, "suspect": self.suspect,
            "new_entities": self.new_entities, "related": self.related,
            "repairs": self.repairs, "repairable": self.repairable,
            "existing_exports": self.existing_exports, "replaced": self.replaced, "rebuild_job": self.rebuild_job,
        }


def _check_taxa(doc: RxfDocument) -> dict[str, str]:
    """Organism name -> NCBI taxid (or 'not_found' / 'unverified'), resolved here, before anything
    is written, and stored in the extraction: materialisation and rebuild never go online."""
    names = list(dict.fromkeys([*doc.paper.organisms, *(d.organism for d in doc.assets.datasets if d.organism)]))
    out: dict[str, str] = {}
    for name in names:
        tid, status = taxonomy_lookup(name)
        out[name] = tid or status
    return out


def _already_verified(s: Session | None, etype: str, external_id: str) -> bool:
    """An anchored entity the library already verified needs no new lookup (rate limits)."""
    if s is None:
        return False
    e = s.execute(select(Entity).where(Entity.type == etype, Entity.external_id == external_id)).scalar_one_or_none()
    return bool(e is not None and (e.attrs or {}).get("verified") == "verified")


def _check_ids(doc: RxfDocument, s: Session | None = None) -> tuple[dict[str, str], list[str]]:
    checks: dict[str, str] = {}
    for d in doc.assets.datasets:
        if not d.accession:
            continue
        acc = d.accession.strip()
        if not accession_format_ok(acc, d.database):
            checks[acc] = "bad_format"
        elif _already_verified(s, "dataset", acc):
            checks[acc] = "verified"
        else:
            checks[acc] = check_accession(acc, d.database)
    for m in doc.assets.methods:
        if m.repo:
            repo = normalize_repo(m.repo)
            if repo is None:
                # a URL on another host (a lab site, Hugging Face, ...) is kept as a link, not anchored
                checks[m.repo] = "unanchored" if is_url(m.repo) else "bad_format"
            elif _already_verified(s, "method", repo):
                checks[repo] = "verified"
            else:
                checks[repo] = check_repo(repo)
    suspect = [k for k, v in checks.items() if v in ("not_found", "bad_format")]
    return checks, suspect


_FETCH = object()
MAX_TEXT_CHARS = 5_000_000
MAX_PDF_BYTES = 200_000_000


def ingest_text(s: Session, text: str, filename: str = "inline.yaml", pdf: bytes | None = None,
                recall: bool = True, repair: bool = False, replace: bool = False, *,
                openalex_record: Any = _FETCH, captured_at: datetime | None = None) -> IngestResult:
    """``repair`` applies the known-drift fixes (rxf/repair.py): L0 keeps the original bytes, the L1
    row holds the repaired document and records the fixes in ``meta.repairs``. ``openalex_record``
    (a stored response or None) and ``captured_at`` replay a capture from raw/ without going
    online and with its original time."""
    from ..i18n import _

    if len(text) > MAX_TEXT_CHARS:
        return IngestResult(ok=False, report=_("ingest.too_large", what="RXF", mb=round(len(text) / 1e6, 1)))
    if pdf is not None:
        if not pdf.startswith(b"%PDF"):
            return IngestResult(ok=False, report=_("ingest.not_pdf"))
        if len(pdf) > MAX_PDF_BYTES:
            return IngestResult(ok=False, report=_("ingest.too_large", what="PDF", mb=round(len(pdf) / 1e6, 1)))
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
        out = IngestResult(ok=True, duplicate=True, work_key=e.key if e else raw.work_key, work_id=e.id if e else None)
        if pdf:  # the same export again, now with its PDF: attach it instead of dropping it
            ex = next((x for x in s.execute(select(Extraction).where(Extraction.kind == "rxf")).scalars()
                       if (x.input_hashes or [None])[0] == sha), None)
            if ex is not None and len(ex.input_hashes or []) == 1:
                pdf_sha = rawstore.put(s, pdf, "pdf", Path(filename).with_suffix(".pdf").name, ex.work_key)
                ex.input_hashes = [sha, pdf_sha]
                if doc.depth == "deep":
                    w = s.get(Work, ex.work_key and (e.id if e else -1)) if e else None
                    if w is not None:
                        w.tier = max(w.tier or 0, 2)
                out.pdf_attached = True
            else:
                out.pdf_attached = False
        return out

    max_before = s.execute(select(func.coalesce(func.max(Entity.id), 0))).scalar_one()

    # resolve canonical work id (T0) and verify external ids before anything enters the graph
    if openalex_record is _FETCH:
        oa, oa_status = openalex.fetch(doi=doc.paper.doi) if doc.paper.doi else (None, "no_doi")
    else:
        oa, oa_status = openalex_record, "ok" if openalex_record else "replayed"
    checks, suspect = _check_ids(doc, s)
    meta = {"openalex_id": oa["id"] if oa else None, "checks": checks, "suspect": suspect,
            "filename": filename, "taxa": _check_taxa(doc), "openalex": oa_status}
    if res.repairs:
        meta["repairs"] = res.repairs
    wkey = work_key(meta["openalex_id"], doc.paper.doi, doc.paper.title)

    hashes = [rawstore.put(s, data, "rxf", filename, wkey)]
    if pdf:
        hashes.append(rawstore.put(s, pdf, "pdf", Path(filename).with_suffix(".pdf").name, wkey))
    oa_sha = None
    if oa:  # the response is L0 too: a replay from raw/ must not depend on OpenAlex being up
        oa_sha = rawstore.put(s, json.dumps(oa, ensure_ascii=False, sort_keys=True).encode("utf-8"), "openalex",
                              f"openalex:{oa['id']}", wkey)
    now = captured_at or utcnow()
    rawstore.write_meta(sha, {"filename": filename, "captured_at": now.isoformat(), "pdf_sha": hashes[1] if pdf else None,
                              "openalex_sha": oa_sha, "repairs": res.repairs, "replace": replace,
                              "work_key": wkey})

    ex_oa = attach_openalex(g, wkey, oa, doc.paper.doi, created_at=now) if oa else None

    ex = Extraction(kind="rxf", work_key=wkey, tier=2 if pdf and doc.depth == "deep" else 1,
                    model="chat-export", prompt_version=doc.prompt_version,
                    schema_version=f"rxf-v{doc.rxf_version}", input_hashes=hashes,
                    output=doc.model_dump(mode="json"), meta=meta, created_at=now)
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
    promote_topics(g, only=out.topics)
    s.flush()
    out.work = g.by_key(out.work.key) or out.work
    new = [e.key for e in s.execute(select(Entity).where(Entity.id > max_before)).scalars()]
    result = IngestResult(ok=True, work_key=out.work.key, work_id=out.work.id, suspect=suspect, new_entities=new,
                          repairs=res.repairs)
    earlier = list(s.execute(select(Extraction.id).where(
        Extraction.work_key == out.work.key, Extraction.kind == "rxf", Extraction.is_current,
        Extraction.id != ex.id).order_by(Extraction.id)).scalars())
    if earlier and replace:
        # a corrected re-export supersedes the earlier ones: a retract decision (undoable), and a
        # rebuild drops what only the old exports asserted
        from .. import jobs
        from . import decisions

        decisions.record(g, "retract", {"work": out.work.key, "extraction_ids": earlier, "reason": "replaced"})
        result.replaced = earlier
        result.rebuild_job = jobs.enqueue(s, "rebuild", {}).id
    elif earlier:
        result.existing_exports = earlier
    if recall:
        from ..services.recall import related_to_work

        result.related = related_to_work(s, out.work.id)
    from .. import jobs

    jobs.maybe_agent_review(s)  # new topic questions: the agent looks at them in the background
    return result


def attach_openalex(g: Graph, wkey: str, oa: dict, doi: str | None,
                    created_at: datetime | None = None) -> Extraction | None:
    """Store an OpenAlex record as an L1 extraction of the paper and materialise it, unless the
    paper already has one (it may exist under its DOI key or its OpenAlex key)."""
    s = g.s
    known = {wkey, f"work:openalex:{oa['id']}"} | ({f"work:doi:{normalize_doi(doi)}"} if doi else set())
    existing_work = next((g.by_key(k) for k in known if g.by_key(k) is not None), None)
    if existing_work is not None:
        known.add(existing_work.key)
    have = s.execute(select(Extraction.id).where(Extraction.kind == "openalex", Extraction.is_current,
                                                 Extraction.work_key.in_(known))).first()
    if have:
        return None
    ex_oa = Extraction(kind="openalex", work_key=existing_work.key if existing_work else wkey, tier=0,
                       model="openalex", prompt_version=None, schema_version="openalex-v1", input_hashes=[],
                       output=oa, meta={}, created_at=created_at or utcnow())
    s.add(ex_oa)
    s.flush()
    materialize_openalex(g, ex_oa)
    return ex_oa


def enrich_missing(s: Session, limit: int = 50) -> dict[str, Any]:
    """Backfill OpenAlex metadata (abstract, venue, references -> cites edges) for papers whose
    lookup failed at ingest (offline, rate limit, outage). Lookups happen first, without holding
    the write lock; then the found records are stored."""
    from ..db.models import Work

    if get_settings().offline:
        return {"offline": True}
    have = set(s.execute(select(Extraction.work_key).where(Extraction.kind == "openalex", Extraction.is_current)).scalars())
    todo = [(e.key, (w.dois or [None])[0]) for e, w in s.execute(
        select(Entity, Work).join(Work, Work.entity_id == Entity.id).where(Entity.type == "work")).all()
        if e.key not in have and w.dois]
    missing = len(todo)
    s.commit()  # read done: release before going online
    found: list[tuple[str, str, dict]] = []
    counts = {"ok": 0, "not_found": 0, "transient": 0, "invalid": 0, "offline": 0}
    for key, doi in todo[:limit]:
        rec, status = openalex.fetch(doi=doi)
        counts[status] = counts.get(status, 0) + 1
        if rec:
            found.append((key, doi, rec))
    g = Graph(s)
    ids = set()
    for key, doi, rec in found:
        ex = attach_openalex(g, key, rec, doi)
        if ex is not None:
            ids.add(g.by_key(ex.work_key).id)
    cites = link_citations(g, only_work_ids=ids) if ids else 0
    return {"missing_before": missing, "checked": min(limit, missing), **counts, "cites": cites}


def read_inbox_file(path: Path) -> tuple[str, bytes | None]:
    """RXF text plus the same-named PDF, if present. Raises RxfEncodingError for a file that is
    not UTF-8 / UTF-16 (the message tells the user to save as UTF-8)."""
    from ..rxf.loader import decode_rxf

    pdf_path = path.with_suffix(".pdf")
    return decode_rxf(path.read_bytes()), (pdf_path.read_bytes() if pdf_path.exists() else None)


def ingest_path(s: Session, path: Path, repair: bool = False, replace: bool = False) -> IngestResult:
    """Ingest an inbox file (and a same-named PDF). Does not move anything: the caller commits first."""
    text, pdf = read_inbox_file(path)
    return ingest_text(s, text, path.name, pdf, repair=repair, replace=replace)


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


def ingest_file(path: Path, repair: bool = False, replace: bool = False) -> IngestResult:
    """Own transaction per file; the move happens only after the commit succeeded."""
    import traceback

    from ..db.session import session_scope

    from ..ml import ModelUnavailable

    from ..rxf.loader import RxfEncodingError

    try:
        with session_scope() as s:
            result = ingest_path(s, path, repair=repair, replace=replace)
    except RxfEncodingError as e:  # the file itself: moved to error/ with the plain explanation
        result = IngestResult(ok=False, report=f"{path.name}: {e}")
        file_done(path, result)
        return result
    except ModelUnavailable as e:  # not the file's fault: leave it in the inbox for after the fix
        log.error("inbox: %s left in place: %s", path.name, e)
        return IngestResult(ok=False, report=str(e))
    except OperationalError as e:
        if "locked" not in str(e).lower() and "busy" not in str(e).lower():
            log.exception("ingest failed for %s", path)
            file_done(path, None, error=f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=8)}")
            return IngestResult(ok=False, report=str(e))
        # a long job holds the write lock: not the file's fault; the periodic rescan retries it
        log.warning("inbox: %s left in place, library busy: %s", path.name, e)
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


def recover_from_raw(s: Session) -> dict[str, Any]:
    """Replay every capture record in raw/ (oldest first) that the database does not have: the
    original bytes, the paired PDF and the OpenAlex response stored at the time, under the
    original capture time. Offline by construction. Captures already present are skipped."""
    done = skipped = failed = 0
    problems: list[dict[str, Any]] = []
    present = _captures(s)
    for m in rawstore.list_meta():
        sha = m["sha"]
        if sha in present:
            skipped += 1
            continue
        data = rawstore.read(sha, "rxf")
        if data is None:
            problems.append({"sha": sha, "error": "raw file missing"})
            failed += 1
            continue
        pdf = rawstore.read(m["pdf_sha"], "pdf") if m.get("pdf_sha") else None
        oa = None
        if m.get("openalex_sha"):
            blob = rawstore.read(m["openalex_sha"], "openalex")
            oa = json.loads(blob) if blob else None
        try:
            when = datetime.fromisoformat(m["captured_at"]) if m.get("captured_at") else None
        except ValueError:
            when = None
        r = ingest_text(s, data.decode("utf-8-sig"), m.get("filename") or f"{sha}.yaml", pdf, recall=False,
                        repair=bool(m.get("repairs")), replace=bool(m.get("replace")),
                        openalex_record=oa, captured_at=when)
        if r.ok:
            done += 1
        else:
            failed += 1
            problems.append({"sha": sha, "error": (r.report or "")[:300]})
        s.commit()
    return {"recovered": done, "already_present": skipped, "failed": failed, "problems": problems}


def _captures(s: Session) -> set[str]:
    """sha of every RXF capture the database already holds (current or retracted)."""
    return {h[0] for h in s.execute(select(Extraction.input_hashes).where(Extraction.kind == "rxf")).scalars() if h}
