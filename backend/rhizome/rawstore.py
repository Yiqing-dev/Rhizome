# SPDX-License-Identifier: Apache-2.0
"""L0: content-addressed, append-only raw store under <data_dir>/raw/ab/<sha256>.<ext>."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from .config import get_settings
from .db.models import RawObject
from .text import sha256


def path_for(sha: str, kind: str) -> Path:
    ext = {"rxf": "yaml", "pdf": "pdf"}.get(kind, "bin")
    return get_settings().raw_dir / sha[:2] / f"{sha}.{ext}"


def put(s: Session, data: bytes, kind: str, uri: str, work_key: str | None = None) -> str:
    sha = sha256(data)
    p = path_for(sha, kind)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
    obj = s.get(RawObject, sha)
    if obj is None:
        s.add(RawObject(sha256=sha, kind=kind, uri=uri, work_key=work_key))
    elif work_key and not obj.work_key:
        obj.work_key = work_key
    return sha


def read(sha: str, kind: str) -> bytes | None:
    p = path_for(sha, kind)
    return p.read_bytes() if p.exists() else None
