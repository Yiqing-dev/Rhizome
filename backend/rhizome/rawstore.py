# SPDX-License-Identifier: Apache-2.0
"""L0: content-addressed, append-only raw store under <data_dir>/raw/ab/<sha256>.<ext>.

Files are written to a unique temporary name in the same folder, flushed and fsynced, then
renamed into place: after a crash a raw object either exists complete or not at all, and two
processes storing the same bytes cannot trip over each other's temporary file."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db.models import RawObject
from .text import sha256


def path_for(sha: str, kind: str) -> Path:
    ext = {"rxf": "yaml", "pdf": "pdf", "openalex": "json"}.get(kind, "bin")
    return get_settings().raw_dir / sha[:2] / f"{sha}.{ext}"


def meta_path(sha: str) -> Path:
    """<sha>.meta.json next to an RXF object: what the database knew at capture time (file name,
    time, the PDF and OpenAlex objects that go with it, repairs), so raw/ alone can rebuild the
    library (``rhz recover --from-raw``). Written once, never updated."""
    return get_settings().raw_dir / sha[:2] / f"{sha}.meta.json"


def write_meta(sha: str, meta: dict[str, Any]) -> None:
    p = meta_path(sha)
    if not p.exists():
        write_durable(p, json.dumps(meta, ensure_ascii=False, indent=1, default=str).encode("utf-8"))


def read_meta(sha: str) -> dict[str, Any] | None:
    p = meta_path(sha)
    try:
        return json.loads(p.read_text("utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


def list_meta() -> list[dict[str, Any]]:
    """Every capture record, oldest first."""
    raw = get_settings().raw_dir
    out = []
    for p in (raw.rglob("*.meta.json") if raw.is_dir() else []):
        try:
            m = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        m["sha"] = p.name[: -len(".meta.json")]
        out.append(m)
    return sorted(out, key=lambda m: (m.get("captured_at") or "", m["sha"]))


def write_durable(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if sys.platform != "win32":  # the rename itself must reach the disk
        try:
            dfd = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass


def put(s: Session, data: bytes, kind: str, uri: str, work_key: str | None = None) -> str:
    sha = sha256(data)
    p = path_for(sha, kind)
    if not p.exists() or p.stat().st_size != len(data):  # a truncated file from a crash is rewritten
        write_durable(p, data)
    obj = s.get(RawObject, sha)
    if obj is None:
        s.add(RawObject(sha256=sha, kind=kind, uri=uri, work_key=work_key))
    elif work_key and not obj.work_key:
        obj.work_key = work_key
    return sha


def read(sha: str, kind: str) -> bytes | None:
    p = path_for(sha, kind)
    if not p.exists():
        import logging

        logging.getLogger(__name__).warning("raw object missing: %s (rhz check lists them)", p.name)
        return None
    return p.read_bytes()


def check(s: Session) -> dict[str, Any]:
    """`rhz diag`: raw objects the database knows but the folder lacks (or that are empty), and
    temporary files an interrupted write left behind."""
    missing: list[str] = []
    empty: list[str] = []
    for sha, kind in s.execute(select(RawObject.sha256, RawObject.kind)).all():
        p = path_for(sha, kind)
        if not p.exists():
            missing.append(p.name)
        elif p.stat().st_size == 0:
            empty.append(p.name)
    raw = get_settings().raw_dir
    stray = sorted(str(t.relative_to(raw)) for t in raw.rglob("*.tmp")) if raw.is_dir() else []
    return {"missing": missing, "empty": empty, "stray_tmp": stray,
            "ok": not (missing or empty or stray)}
