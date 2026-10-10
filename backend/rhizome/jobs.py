# SPDX-License-Identifier: Apache-2.0
"""Task table + background worker (no Redis). Every step is an idempotent batch that can be re-run."""

from __future__ import annotations

import logging
import os
import threading
import traceback
from datetime import datetime, timedelta
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


# Kinds whose result only depends on the library state: a second request while one is still
# queued is the same work, so it is coalesced instead of running the whole thing twice.
COALESCE = ("rebuild", "nightly", "enrich", "agent_review")


def enqueue(s: Session, kind: str, payload: dict[str, Any] | None = None) -> Job:
    if kind not in HANDLERS:
        raise ValueError(f"unknown job kind {kind}")
    payload = payload or {}
    if kind in COALESCE:
        for j in s.execute(select(Job).where(Job.kind == kind, Job.status == "queued")).scalars():
            if (j.payload or {}) == payload:
                return j
    j = Job(kind=kind, payload=payload)
    s.add(j)
    s.flush()
    return j


def active_jobs(s: Session) -> list[Job]:
    return list(s.execute(select(Job).where(Job.status.in_(("queued", "running"))).order_by(Job.id)).scalars())


def running(s: Session, kind: str) -> bool:
    return s.execute(select(Job.id).where(Job.kind == kind, Job.status == "running")).first() is not None


def job_view(j: Job) -> dict[str, Any]:
    return {"id": j.id, "kind": j.kind, "status": j.status, "payload": j.payload, "result": j.result,
            "error": j.error, "created_at": j.created_at.isoformat() if j.created_at else None,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None}


def _finish(job_id: int, **values: Any) -> None:
    """Write the outcome only if this process still owns the job (a restart may have requeued it)."""
    with session_scope() as s:
        s.execute(update(Job).where(Job.id == job_id, Job.status == "running", Job.owner_pid == os.getpid())
                  .values(finished_at=utcnow(), **values))


def run_next(only_id: int | None = None) -> Job | None:
    """Claim and run one queued job (the oldest, or ``only_id``) in its own transaction."""
    with session_scope() as s:
        q = select(Job).where(Job.status == "queued")
        q = q.where(Job.id == only_id) if only_id is not None else q.order_by(Job.id).limit(1)
        j = s.execute(q).scalar_one_or_none()
        if j is None:
            return None
        claimed = s.execute(update(Job).where(Job.id == j.id, Job.status == "queued")
                            .values(status="running", started_at=utcnow(), owner_pid=os.getpid())).rowcount
        if not claimed:
            return None
        job_id, kind, payload = j.id, j.kind, dict(j.payload or {})
    try:
        with session_scope() as s:
            result = HANDLERS[kind](s, payload)
        _finish(job_id, status="done", result=result)
    except Exception as e:
        log.exception("job %s (%s) failed", job_id, kind)
        try:
            _finish(job_id, status="failed", error=f"{e}\n{traceback.format_exc()[-8000:]}")
            if kind == "nightly":
                with session_scope() as s:
                    _kv_set(s, "last_nightly_failure", {"at": utcnow().isoformat()})
        except Exception:  # noqa: BLE001 - the database itself may be the problem
            log.exception("could not record the failure of job %s", job_id)
    with session_scope() as s:
        return s.get(Job, job_id)


def run_job(job_id: int) -> Job | None:
    """Run exactly this job in the calling process (CLI, LocalClient) without draining the queue
    that belongs to the app's worker."""
    return run_next(only_id=job_id)


def run_all() -> int:
    n = 0
    while run_next() is not None:
        n += 1
    return n


STALE_RUNNING = timedelta(hours=6)
CHECK_EVERY = 300.0  # seconds between nightly / daily-backup checks


def recover_stale_jobs(s: Session) -> int:
    """Jobs left `running` by a process that is gone (crash, kill, power cut) are re-queued; every
    handler is idempotent. A job another live process is running (the CLI, a second app) is left
    alone."""
    from .system import pid_alive

    n = 0
    for j in s.execute(select(Job).where(Job.status == "running")).scalars():
        if j.owner_pid is None or (j.owner_pid != os.getpid() and not pid_alive(j.owner_pid)):
            j.status, j.started_at, j.owner_pid, j.error = "queued", None, None, "requeued after restart"
            n += 1
    return n


def maybe_schedule_nightly(s: Session) -> Job | None:
    row = s.get(KV, "last_nightly")
    last = row.v.get("at") if row and row.v else None
    from datetime import datetime

    if last and utcnow() - datetime.fromisoformat(last) < timedelta(hours=24):
        return None
    fail = s.get(KV, "last_nightly_failure")
    failed_at = fail.v.get("at") if fail and fail.v else None
    if failed_at and utcnow() - datetime.fromisoformat(failed_at) < timedelta(hours=6):
        return None  # back off: a failing nightly would otherwise rerun every 30 minutes
    pending = s.execute(select(Job).where(
        Job.kind == "nightly",
        (Job.status == "queued") | ((Job.status == "running") & (Job.started_at > utcnow() - STALE_RUNNING)),
    )).first()
    if pending:
        return None
    return enqueue(s, "nightly")


class Worker(threading.Thread):
    def __init__(self, interval: float = 2.0):
        super().__init__(daemon=True, name="rhizome-worker")
        self.interval = interval
        self._halt = threading.Event()  # not `_stop`: threading.Thread._stop() is a method
        self._last_check = 0.0

    def stop(self) -> None:
        self._halt.set()

    @staticmethod
    def _daily_backup() -> None:
        from .db.session import maybe_daily_backup

        try:
            path = maybe_daily_backup(get_settings())
            if path:
                log.info("daily backup written to %s", path)
        except Exception:  # a missing backup folder must not stop the worker
            log.exception("daily backup failed")

    def startup(self) -> None:
        try:
            with session_scope() as s:
                recover_stale_jobs(s)
        except Exception:
            log.exception("could not recover stale jobs")
        self._daily_backup()
        self._last_check = 0.0

    def step(self, now: float | None = None) -> bool:
        """One pass of the loop (tested synchronously): flush buffered views; by wall clock, at
        start and every CHECK_EVERY seconds, busy or not, the daily backup and the nightly check
        (short desktop sessions must still get them); then one job. Returns whether a job ran."""
        import time

        from .services.recall import flush_seen

        now = time.monotonic() if now is None else now
        flush_seen()
        if now - self._last_check > CHECK_EVERY or self._last_check == 0.0:
            self._last_check = now
            self._daily_backup()
            with session_scope() as s:
                maybe_schedule_nightly(s)
            try:
                maybe_check_updates()
            except Exception:  # noqa: BLE001 - never more than a log line
                log.exception("update check failed")
        return run_next() is not None

    def run(self) -> None:  # pragma: no cover - the loop glue; step() is what the tests run
        self.startup()
        while not self._halt.is_set():
            try:
                if not self.step():
                    self._halt.wait(self.interval)
            except Exception:
                log.exception("worker loop error")
                self._halt.wait(self.interval * 5)


# ---- handlers ------------------------------------------------------------------------

@handler("rebuild")
def _rebuild(s: Session, p: dict[str, Any]) -> dict[str, Any]:
    from .pipeline.rebuild import rebuild

    return rebuild(s, backup=p.get("backup", True), force=p.get("force", False))


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
        # catch up on everything added since the last batch (weeks without a run included)
        out["synthesis_candidates"] = synthesis.generate_candidates(
            s, days=7, since=datetime.fromisoformat(last) if last else None)
        _kv_set(s, "last_synthesis", {"at": utcnow().isoformat()})
    _kv_set(s, "last_nightly", {"at": utcnow().isoformat()})
    maybe_agent_review(s)
    if not get_settings().offline:
        enqueue(s, "enrich", {})  # metadata that failed at ingest time (separate job: goes online)
    return out


@handler("agent_review")
def _agent_review(s: Session, p: dict[str, Any]) -> dict[str, Any]:
    from .inference.agent import DEFAULT_LIMIT, review_with_agent

    return review_with_agent(s, limit=int(p.get("limit", DEFAULT_LIMIT)), item_ids=p.get("item_ids"),
                             force=bool(p.get("force")))


@handler("organise_topic")
def _organise_topic(s: Session, p: dict[str, Any]) -> dict[str, Any]:
    from .inference.organise import organise_topic

    return organise_topic(s, p["topic"])


def maybe_agent_review(s: Session) -> Job | None:
    """With the Anthropic backend selected, unjudged topic questions get a run of the topic agent."""
    if get_settings().inference_backend != "anthropic":
        return None
    from .inference.agent import pending_unjudged

    return enqueue(s, "agent_review", {}) if pending_unjudged(s) else None


@handler("enrich")
def _enrich(s: Session, p: dict[str, Any]) -> dict[str, Any]:
    from .pipeline.ingest import enrich_missing

    return enrich_missing(s, limit=int(p.get("limit", 50)))


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


# ---- update notice -------------------------------------------------------------------------

RELEASES_URL = "https://api.github.com/repos/Yiqing-dev/Rhizome/releases/latest"
UPDATE_KEY = "update_check"
UPDATE_EVERY = timedelta(hours=24)


def version_tuple(v: str) -> tuple[int, ...]:
    out = []
    for part in str(v).lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def maybe_check_updates(force: bool = False) -> dict[str, Any] | None:
    """Once a day (settings.check_updates, never offline): the newest published release, kept in
    KV so the home page can say a fixed version exists. Returns the stored record."""
    from . import __version__
    from .external.verify import get

    st = get_settings()
    with session_scope() as s:
        row = s.get(KV, UPDATE_KEY)
        rec = dict(row.v or {}) if row else {}
    if not st.check_updates or st.offline:
        return rec or None
    last = rec.get("checked_at")
    if not force and last and utcnow() - datetime.fromisoformat(last) < UPDATE_EVERY:
        return rec
    try:
        r = get(RELEASES_URL, retries=0)
        if r.status_code != 200:
            return rec or None
        body = r.json()
    except Exception as e:  # noqa: BLE001 - offline laptops, rate limits: try again tomorrow
        log.info("update check skipped: %s", e)
        return rec or None
    latest = str(body.get("tag_name") or "").lstrip("v")
    rec = {"checked_at": utcnow().isoformat(), "latest": latest, "url": body.get("html_url"),
           "newer": bool(latest) and version_tuple(latest) > version_tuple(__version__)}
    with session_scope() as s:
        _kv_set(s, UPDATE_KEY, rec)
    return rec


def update_notice(s: Session) -> dict[str, Any] | None:
    """{latest, url} when a newer release is known, else None (read by the home page)."""
    from . import __version__

    row = s.get(KV, UPDATE_KEY)
    rec = (row.v or {}) if row else {}
    if rec.get("latest") and version_tuple(rec["latest"]) > version_tuple(__version__):
        return {"latest": rec["latest"], "url": rec.get("url"), "current": __version__}
    return None
