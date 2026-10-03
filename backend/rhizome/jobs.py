# SPDX-License-Identifier: Apache-2.0
"""Task table + background worker (no Redis). Every step is an idempotent batch that can be re-run."""

from __future__ import annotations

import logging
import threading
import traceback
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .db.models import KV, Job, utcnow
from .db.session import session_scope

log = logging.getLogger(__name__)

Handler = Callable[[Session, dict[str, Any]], dict[str, Any]]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


def enqueue(s: Session, kind: str, payload: dict[str, Any] | None = None) -> Job:
    if kind not in HANDLERS:
        raise ValueError(f"unknown job kind {kind}")
    j = Job(kind=kind, payload=payload or {})
    s.add(j)
    s.flush()
    return j


def job_view(j: Job) -> dict[str, Any]:
    return {"id": j.id, "kind": j.kind, "status": j.status, "payload": j.payload, "result": j.result,
            "error": j.error, "created_at": j.created_at.isoformat() if j.created_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None}


def run_next() -> Job | None:
    """Claim and run one queued job in its own transaction."""
    with session_scope() as s:
        j = s.execute(select(Job).where(Job.status == "queued").order_by(Job.id).limit(1)).scalar_one_or_none()
        if j is None:
            return None
        claimed = s.execute(update(Job).where(Job.id == j.id, Job.status == "queued")
                            .values(status="running", started_at=utcnow())).rowcount
        if not claimed:
            return None
        job_id, kind, payload = j.id, j.kind, dict(j.payload or {})
    try:
        with session_scope() as s:
            result = HANDLERS[kind](s, payload)
        with session_scope() as s:
            j = s.get(Job, job_id)
            j.status, j.result, j.finished_at = "done", result, utcnow()
    except Exception as e:
        log.exception("job %s (%s) failed", job_id, kind)
        with session_scope() as s:
            j = s.get(Job, job_id)
            j.status, j.error, j.finished_at = "failed", f"{e}\n{traceback.format_exc(limit=5)}", utcnow()
    with session_scope() as s:
        return s.get(Job, job_id)


def run_all() -> int:
    n = 0
    while run_next() is not None:
        n += 1
    return n


def maybe_schedule_nightly(s: Session) -> Job | None:
    row = s.get(KV, "last_nightly")
    last = row.v.get("at") if row and row.v else None
    from datetime import datetime

    if last and utcnow() - datetime.fromisoformat(last) < timedelta(hours=24):
        return None
    pending = s.execute(select(Job).where(Job.kind == "nightly", Job.status.in_(("queued", "running")))).first()
    if pending:
        return None
    return enqueue(s, "nightly")


class Worker(threading.Thread):
    def __init__(self, interval: float = 2.0):
        super().__init__(daemon=True, name="rhizome-worker")
        self.interval = interval
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # pragma: no cover - exercised by the app, not unit tests
        ticks = 0
        while not self._stop.is_set():
            try:
                if run_next() is None:
                    ticks += 1
                    if ticks % 900 == 0:  # roughly every 30 minutes
                        with session_scope() as s:
                            maybe_schedule_nightly(s)
                    self._stop.wait(self.interval)
            except Exception:
                log.exception("worker loop error")
                self._stop.wait(self.interval * 5)


# ---- handlers ------------------------------------------------------------------------

@handler("rebuild")
def _rebuild(s: Session, p: dict[str, Any]) -> dict[str, Any]:
    from .pipeline.rebuild import rebuild

    return rebuild(s, backup=p.get("backup", True))


@handler("retro_tag")
def _retro(s: Session, p: dict[str, Any]) -> dict[str, Any]:
    from .pipeline.retro import retro_tag

    return retro_tag(s, p["topic"], p.get("k"))


@handler("nightly")
def _nightly(s: Session, p: dict[str, Any]) -> dict[str, Any]:
    from .pipeline.graph import Graph
    from .pipeline.materialize import promote_topics
    from .services import communities, synthesis

    comm = communities.detect(s)
    promoted = promote_topics(Graph(s))
    out: dict[str, Any] = {"communities": len(set(comm.values())), "topics_promoted": promoted}
    row = s.get(KV, "last_synthesis")
    last = row.v.get("at") if row and row.v else None
    from datetime import datetime

    if p.get("force_synthesis") or not last or utcnow() - datetime.fromisoformat(last) >= timedelta(days=7):
        out["synthesis_candidates"] = synthesis.generate_candidates(s)
        _kv_set(s, "last_synthesis", {"at": utcnow().isoformat()})
    _kv_set(s, "last_nightly", {"at": utcnow().isoformat()})
    return out


@handler("sync")
def _sync(s: Session, p: dict[str, Any]) -> dict[str, Any]:
    from .services.snapshot import sync

    st = get_settings()
    target = next((r for r in st.remotes if r.name == p["remote"]), None)
    if target is None:
        raise LookupError(f"remote {p['remote']} not configured")
    s.commit()
    cmds = sync(st, target)
    return {"remote": target.name, "steps": len(cmds)}


def _kv_set(s: Session, k: str, v: Any) -> None:
    row = s.get(KV, k)
    if row is None:
        s.add(KV(k=k, v=v))
    else:
        row.v = v
