# SPDX-License-Identifier: Apache-2.0
"""Imports from the app (drop zone / file picker) as one server-side batch with visible progress.

The files are uploaded once into ``<data>/imports/<batch>/``, paired (RXF by content, a PDF by the
same file stem), and a worker job ingests them one by one. Progress lives in the KV table, so the
UI can always find it again: after a page reload, after the window was minimised, or after the app
restarted (a job left running is re-queued, and files that already have a result are skipped).

Only one batch is active at a time; it stays active, with its per-file results, until the user
dismisses it. Files that failed stay staged so a repairable one can be imported again with the
known-drift fixes.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..config import get_settings
from ..db.models import KV, Job, utcnow

ACTIVE = "import:active"
RXF_SUFFIXES = (".yaml", ".yml", ".rxf")


class ImportBusy(RuntimeError):
    """Another import is still running or waiting to be dismissed."""


class NothingToImport(ValueError):
    """No RXF among the uploaded files."""


def _dir(batch: str) -> Path:
    return get_settings().data_dir / "imports" / batch


def _key(batch: str) -> str:
    return f"import:{batch}"


def progress(s: Session, batch: str) -> dict[str, Any] | None:
    row = s.get(KV, _key(batch))
    return dict(row.v) if row is not None and isinstance(row.v, dict) else None


def _save(s: Session, prog: dict[str, Any]) -> None:
    row = s.get(KV, _key(prog["batch"]))
    if row is None:
        s.add(KV(k=_key(prog["batch"]), v=prog))
    else:
        row.v = prog
        flag_modified(row, "v")
    s.flush()


def active(s: Session) -> dict[str, Any] | None:
    """The batch the UI must show (running, queued, or finished but not dismissed). A batch whose
    job ended without finishing it (the app was killed and the job could not resume) is reported
    as interrupted instead of running forever."""
    row = s.get(KV, ACTIVE)
    batch = (row.v or {}).get("batch") if row is not None and isinstance(row.v, dict) else None
    prog = progress(s, batch) if batch else None
    if prog is None:
        return None
    if prog.get("status") in ("queued", "running") and prog.get("job"):
        j = s.get(Job, prog["job"])
        if j is None or j.status in ("done", "failed"):
            prog["status"] = "interrupted"
            prog["error"] = (j.error or "")[:500] if j is not None else "the import job is gone"
            prog["current"] = None
            _save(s, prog)
    return prog


def _unique(name: str, taken: set[str]) -> str:
    name = Path(name).name or "upload"
    stem, suffix = Path(name).stem, Path(name).suffix
    out, i = name, 2
    while out.lower() in taken:
        out = f"{stem} ({i}){suffix}"
        i += 1
    taken.add(out.lower())
    return out


def stage(s: Session, uploads: list[tuple[str, bytes]], repair: bool = False) -> dict[str, Any]:
    """Store the uploaded files for a new batch, pair them, and queue the import job."""
    from .. import jobs
    from ..inbox import looks_like_rxf

    current = active(s)
    if current is not None:
        raise ImportBusy(current["batch"])
    batch = uuid.uuid4().hex[:12]
    d = _dir(batch)
    d.mkdir(parents=True, exist_ok=True)
    taken: set[str] = set()
    paths: list[Path] = []
    for name, data in uploads:
        p = d / _unique(name, taken)
        p.write_bytes(data)
        paths.append(p)
    pdfs = {p.stem.lower(): p for p in paths if p.suffix.lower() == ".pdf"}
    files, skipped, used_pdfs = [], [], set()
    for p in paths:
        if p.suffix.lower() == ".pdf":
            continue
        if p.suffix.lower() in RXF_SUFFIXES or looks_like_rxf(p):
            pdf = pdfs.get(p.stem.lower())
            if pdf is not None:
                used_pdfs.add(pdf.name)
            files.append({"name": p.name, "pdf": pdf.name if pdf is not None else None})
        else:
            skipped.append(p.name)
    skipped += [p.name for p in pdfs.values() if p.name not in used_pdfs]
    if not files:
        shutil.rmtree(d, ignore_errors=True)
        raise NothingToImport()
    for name in skipped:
        (d / name).unlink(missing_ok=True)
    job = jobs.enqueue(s, "ingest_batch", {"batch": batch, "repair": repair})
    prog = {"batch": batch, "status": "queued", "job": job.id, "files": files, "skipped": skipped,
            "results": {}, "total": len(files), "done": 0, "current": None, "repair": repair,
            "created_at": utcnow().isoformat(), "finished_at": None}
    _save(s, prog)
    row = s.get(KV, ACTIVE)
    if row is None:
        s.add(KV(k=ACTIVE, v={"batch": batch}))
    else:
        row.v = {"batch": batch}
    s.flush()
    return prog


def run_batch(s: Session, batch: str, only: list[str] | None = None, repair: bool = False) -> dict[str, Any]:
    """The job: ingest every staged file that has no result yet (or exactly `only`)."""
    from ..pipeline.ingest import ingest_text
    from ..rxf.loader import RxfEncodingError, decode_rxf

    prog = progress(s, batch)
    if prog is None:
        return {"batch": batch, "missing": True}
    names = only or [f["name"] for f in prog["files"] if f["name"] not in prog["results"]]
    pdf_of = {f["name"]: f.get("pdf") for f in prog["files"]}
    prog.update(status="running", total=len(names) if only else prog["total"],
                done=0 if only else len(prog["results"]), current=None, error=None)
    _save(s, prog)
    s.commit()
    d = _dir(batch)
    for name in names:
        prog["current"] = name
        _save(s, prog)
        s.commit()
        out: dict[str, Any] = {"name": name}
        try:
            text = decode_rxf((d / name).read_bytes())
            pdf = (d / pdf_of[name]).read_bytes() if pdf_of.get(name) and (d / pdf_of[name]).exists() else None
            res = ingest_text(s, text, name, pdf, repair=repair)
            if res.ok:
                s.commit()
                out.update(ok=True, work_key=res.work_key, work_id=res.work_id, duplicate=res.duplicate,
                           repairs=res.repairs, related=res.related[:5])
                (d / name).unlink(missing_ok=True)  # kept in raw/ now; only failures stay staged
                if pdf_of.get(name):
                    (d / pdf_of[name]).unlink(missing_ok=True)
            else:
                s.rollback()
                out.update(ok=False, report=res.report, repairable=res.repairable)
        except RxfEncodingError as e:
            s.rollback()
            out.update(ok=False, report=str(e), repairable=[])
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the batch
            s.rollback()
            out.update(ok=False, report=f"{type(e).__name__}: {e}", repairable=[])
        prog = progress(s, batch) or prog  # re-read after a rollback
        prog["results"][name] = out
        prog["done"] = prog.get("done", 0) + 1
        _save(s, prog)
        s.commit()
    prog.update(status="done", current=None, finished_at=utcnow().isoformat())
    _save(s, prog)
    ok = sum(1 for r in prog["results"].values() if r.get("ok"))
    return {"batch": batch, "ok": ok, "failed": len(prog["results"]) - ok}


def repair_failed(s: Session, batch: str) -> dict[str, Any]:
    """Import the repairable failures of a finished batch again, with the known-drift fixes."""
    from .. import jobs

    prog = progress(s, batch)
    if prog is None or prog.get("status") not in ("done", "interrupted"):
        raise ImportBusy(batch)
    names = [n for n, r in prog["results"].items() if not r.get("ok") and r.get("repairable")]
    if not names:
        return prog
    job = jobs.enqueue(s, "ingest_batch", {"batch": batch, "only": names, "repair": True})
    prog.update(status="queued", job=job.id, total=len(names), done=0)
    _save(s, prog)
    return prog


def dismiss(s: Session, batch: str) -> bool:
    """Close a finished batch: the UI is free again and the staged leftovers are removed."""
    prog = progress(s, batch)
    if prog is not None and prog.get("status") in ("queued", "running"):
        raise ImportBusy(batch)
    row = s.get(KV, ACTIVE)
    if row is not None and isinstance(row.v, dict) and row.v.get("batch") == batch:
        s.delete(row)
    k = s.get(KV, _key(batch))
    if k is not None:
        s.delete(k)
    shutil.rmtree(_dir(batch), ignore_errors=True)
    return prog is not None
