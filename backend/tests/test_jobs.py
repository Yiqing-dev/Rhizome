# SPDX-License-Identifier: Apache-2.0
"""Jobs and concurrency: coalescing, ownership-aware recovery, nightly back-off, write-free reads,
a fast 'busy' answer during a rebuild."""

import os

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from rhizome import jobs
from rhizome.db.models import Job, VectorCache
from rhizome.db.session import session_scope


def test_rebuild_and_nightly_requests_coalesce(settings):
    with session_scope() as s:
        a = jobs.enqueue(s, "rebuild", {})
        b = jobs.enqueue(s, "rebuild", {})
        c = jobs.enqueue(s, "rebuild", {"backup": False})  # different work: its own job
        d = jobs.enqueue(s, "nightly", {})
        e = jobs.enqueue(s, "nightly", {})
        assert a.id == b.id and c.id != a.id and d.id == e.id


def test_recovery_only_requeues_jobs_of_dead_processes(settings):
    with session_scope() as s:
        dead = Job(kind="rebuild", payload={}, status="running", owner_pid=2**22 + 12345)
        alive = Job(kind="rebuild", payload={"x": 1}, status="running", owner_pid=os.getppid())
        legacy = Job(kind="nightly", payload={}, status="running", owner_pid=None)
        s.add_all([dead, alive, legacy])
        s.flush()
        ids = dead.id, alive.id, legacy.id
    with session_scope() as s:
        assert jobs.recover_stale_jobs(s) == 2
    with session_scope() as s:
        assert [s.get(Job, i).status for i in ids] == ["queued", "running", "queued"]


def test_a_requeued_job_is_not_overwritten_by_its_old_runner(settings):
    with session_scope() as s:
        j = Job(kind="rebuild", payload={}, status="queued", owner_pid=None)
        s.add(j)
        s.flush()
        jid = j.id
    jobs._finish(jid, status="done", result={})  # not running, not ours: no effect
    with session_scope() as s:
        assert s.get(Job, jid).status == "queued"


def test_failing_nightly_backs_off(settings, monkeypatch):
    def boom(s, p):
        raise RuntimeError("community detection crashed")

    monkeypatch.setitem(jobs.HANDLERS, "nightly", boom)
    with session_scope() as s:
        jid = jobs.maybe_schedule_nightly(s).id
    j = jobs.run_job(jid)
    assert j.status == "failed" and "community detection crashed" in j.error and "Traceback" in j.error
    with session_scope() as s:
        assert jobs.maybe_schedule_nightly(s) is None  # not again every 30 minutes


def test_search_does_not_write(library, session):
    from rhizome.services.search import search

    session.commit()
    before = session.execute(select(func.count()).select_from(VectorCache)).scalar_one()
    assert search(session, "a query nobody has typed before about chromatin priors")
    assert session.execute(select(func.count()).select_from(VectorCache)).scalar_one() == before
    assert not session.new and not session.dirty


def test_views_are_buffered_and_flushed_by_the_worker(library, session):
    from rhizome.db.models import AccessLog
    from rhizome.pipeline.graph import Graph
    from rhizome.services import recall
    from rhizome.services.views import entity_card

    e = Graph(session).by_key("dataset:GSE999001")
    session.commit()
    entity_card(session, e.id)
    assert not session.new and not session.dirty  # the read itself writes nothing
    assert recall.forgetting(session, [e])[e.id] < 0.01  # but the view already counts
    assert recall.flush_seen() == 1
    session.expire_all()
    assert session.get(AccessLog, e.key).count == 1


def test_writes_get_busy_answer_during_a_rebuild(settings):
    from rhizome.api.app import create_app

    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    with session_scope() as s:
        s.add(Job(kind="rebuild", payload={}, status="running", owner_pid=os.getpid()))
    r = c.post("/decision", json={"op": "create_topic", "payload": {"name": "x"}})
    assert r.status_code == 503 and r.headers["retry-after"] == "30"
    assert c.get("/stats").status_code == 200  # reads keep working
    assert c.get("/jobs", params={"active": True}).json()["jobs"][0]["kind"] == "rebuild"
    assert c.post("/jobs", json={"kind": "rebuild", "payload": {}}).status_code == 200  # queueing is fine


def test_revoking_twice_queues_one_rebuild(library, session, settings):
    from rhizome.api.app import create_app
    from rhizome.pipeline import decisions
    from rhizome.pipeline.graph import Graph

    d = decisions.record(Graph(session), "create_topic", {"name": "temporary topic"})
    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    first = c.delete(f"/decision/{d.id}").json()
    second = c.delete(f"/decision/{d.id}").json()
    assert first["changed"] and first["rebuild_job"] and not second["changed"] and second["rebuild_job"] is None
