# SPDX-License-Identifier: Apache-2.0
"""Imports from the app run as one server-side batch whose progress the UI can always find again:
pairing by content and stem, one batch at a time, per-file results, repair, resume, dismiss."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import ROOT, example
from rhizome import jobs
from rhizome.db.models import Entity, Job


@pytest.fixture()
def api(settings, session):
    from rhizome.api.app import create_app

    session.commit()
    app = create_app(settings, start_worker=False)  # the test runs the job itself
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    return c


def _upload(api, files, **form):
    return api.post("/import", files=[("files", (n, data)) for n, data in files], data=form)


def _run_all():
    while jobs.run_next() is not None:
        pass


def test_batch_imports_with_progress_and_results(api, settings, session):
    drift = (ROOT / "backend" / "tests" / "fixtures" / "drift-repairable.yaml").read_bytes()
    files = [("深度 导出.yaml", example("deep-grn-atlas.yaml").encode()),
             ("rxf_version 1.txt", drift),  # recognised by content, fails validation, repairable
             ("空间 转录组.yaml", example("light-spatial-domains.yaml").encode()),
             ("空间 转录组.pdf", b"%PDF synthetic"),
             ("notes.txt", b"just a note"), ("orphan.pdf", b"%PDF x")]
    r = _upload(api, files)
    assert r.status_code == 200, r.text
    prog = r.json()
    assert prog["status"] == "queued" and prog["total"] == 3
    assert {f["name"]: f["pdf"] for f in prog["files"]}["空间 转录组.yaml"] == "空间 转录组.pdf"
    assert sorted(prog["skipped"]) == ["notes.txt", "orphan.pdf"]
    # one batch at a time
    assert _upload(api, [("x.yaml", example("light-scenic-benchmark.yaml").encode())]).status_code == 409
    act = api.get("/import/active").json()["active"]
    assert act["batch"] == prog["batch"] and act["status"] == "queued"

    _run_all()
    act = api.get("/import/active").json()["active"]
    assert act["status"] == "done" and act["done"] == 3 and act["current"] is None
    res = act["results"]
    assert res["深度 导出.yaml"]["ok"] and res["深度 导出.yaml"]["work_key"].startswith("work:")
    assert res["空间 转录组.yaml"]["ok"]
    bad = res["rxf_version 1.txt"]
    assert not bad["ok"] and bad["report"] and bad["repairable"]
    card = api.get("/entity/by-key", params={"key": res["空间 转录组.yaml"]["work_key"]}).json()
    assert card["exports"][0]["has_pdf"]
    staged = settings.data_dir / "imports" / prog["batch"]
    assert sorted(p.name for p in staged.iterdir()) == ["rxf_version 1.txt"]  # only the failure stays

    # repair the repairable failure in the same batch
    r = api.post(f"/import/{prog['batch']}/repair")
    assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["total"] == 1
    _run_all()
    act = api.get("/import/active").json()["active"]
    assert act["status"] == "done" and act["results"]["rxf_version 1.txt"]["ok"]
    assert act["results"]["rxf_version 1.txt"]["repairs"]

    # dismiss frees the UI and removes the staging folder
    assert api.delete(f"/import/{prog['batch']}").json() == {"dismissed": True}
    assert api.get("/import/active").json() == {"active": None} and not staged.exists()
    assert _upload(api, [("x.yaml", example("light-scenic-benchmark.yaml").encode())]).status_code == 200


def test_nothing_to_import_and_busy_states(api, settings):
    r = _upload(api, [("notes.txt", b"hello"), ("a.pdf", b"%PDF x")])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_rxf"
    assert not any((settings.data_dir / "imports").iterdir())
    prog = _upload(api, [("a.yaml", example("light-scenic-benchmark.yaml").encode())]).json()
    assert api.delete(f"/import/{prog['batch']}").status_code == 409  # still queued
    assert api.post(f"/import/{prog['batch']}/repair").status_code == 409


def test_resume_after_restart_and_interrupted(api, settings, session):
    from rhizome.services.imports import progress, run_batch

    prog = _upload(api, [("a.yaml", example("deep-grn-atlas.yaml").encode()),
                         ("b.yaml", example("light-scenic-benchmark.yaml").encode())]).json()
    # the app was killed after the first file: the requeued job skips what already has a result
    out = run_batch(session, prog["batch"], only=["a.yaml"])
    session.commit()
    assert out["ok"] == 1
    p = progress(session, prog["batch"])
    p["status"] = "running"
    from rhizome.services.imports import _save

    _save(session, p)
    session.commit()
    _run_all()
    act = api.get("/import/active").json()["active"]
    assert act["status"] == "done" and set(act["results"]) == {"a.yaml", "b.yaml"} and act["done"] == 2
    assert session.execute(select(Entity).where(Entity.type == "work")).scalars().all()
    api.delete(f"/import/{prog['batch']}")

    # a job that ended without finishing the batch is reported, not shown as running forever
    prog = _upload(api, [("c.yaml", example("light-spatial-domains.yaml").encode())]).json()
    j = session.get(Job, prog["job"])
    j.status, j.error = "failed", "boom"
    session.commit()
    act = api.get("/import/active").json()["active"]
    assert act["status"] == "interrupted" and "boom" in act["error"]
    assert api.delete(f"/import/{prog['batch']}").json() == {"dismissed": True}


# ---- cancel / resume, and importing without the model -------------------------------------------

def test_cancel_before_start_then_resume(api, settings, session):
    from rhizome.services import imports

    r = _upload(api, [("a.yaml", example("deep-grn-atlas.yaml").encode()), ("b.yaml", example("light-spatial-domains.yaml").encode())])
    batch = r.json()["batch"]
    assert api.post(f"/import/{batch}/cancel").json()["cancel"] is True
    _run_all()  # the queued job runs, sees the flag, and stops before the first file
    act = api.get("/import/active").json()["active"]
    assert act["status"] == "cancelled" and act["remaining"] == 2 and act["results"] == {}
    assert api.post(f"/import/{batch}/repair").status_code == 200  # nothing repairable: a no-op, not an error
    assert api.post(f"/import/{batch}/resume").json()["status"] == "queued"
    assert batch not in imports.CANCELLED
    _run_all()
    act = api.get("/import/active").json()["active"]
    assert act["status"] == "done" and len(act["results"]) == 2 and all(r["ok"] for r in act["results"].values())
    assert api.delete(f"/import/{batch}").json()["dismissed"] is True
    assert api.post(f"/import/{batch}/cancel").status_code == 404


def test_cancel_mid_batch_stops_after_the_file_in_hand(api, settings, session, monkeypatch):
    from rhizome.services import imports
    from rhizome.pipeline import ingest as ingest_mod

    real = ingest_mod.ingest_text
    seen = []

    def slow_ingest(s, text, name, pdf=None, **kw):
        seen.append(name)
        if len(seen) == 1:
            imports.CANCELLED.add(kw.get("_batch") or next(iter(imports.CANCELLED), None) or batch)  # the user presses cancel while file 1 runs
        return real(s, text, name, pdf, **kw)

    monkeypatch.setattr(imports, "ingest_text", slow_ingest, raising=False)
    import rhizome.services.imports as m
    monkeypatch.setattr("rhizome.pipeline.ingest.ingest_text", slow_ingest)
    r = _upload(api, [("a.yaml", example("deep-grn-atlas.yaml").encode()), ("b.yaml", example("light-spatial-domains.yaml").encode()),
                      ("c.yaml", example("light-scenic-benchmark.yaml").encode())])
    batch = r.json()["batch"]
    _run_all()
    act = api.get("/import/active").json()["active"]
    assert seen == ["a.yaml"] and act["status"] == "cancelled" and act["remaining"] == 2
    assert act["results"]["a.yaml"]["ok"] is True  # the file in hand was finished, not rolled back
    assert m.progress(session, batch)["cancel"] is False


def test_import_without_inference_makes_no_model_calls(api, settings, session, monkeypatch):
    from rhizome.inference import cache, reset_backend
    from rhizome.db.models import KV, ReviewCard

    calls = []

    class Chatty:
        name = "fake"
        model = "m"

        def make_card(self, text):
            calls.append(("card", text[:20]))
            return ("Q?", "A.")

        def judge_breadth(self, a, b):
            calls.append(("breadth", a, b))
            return ("none", 0.9)

        def classify_topic_relation(self, *a):
            return None

    monkeypatch.setattr(cache, "get_backend", lambda: Chatty())
    assert api.patch("/settings", json={"infer_on_import": False}).json()["infer_on_import"] is False
    assert api.get("/stats").json()["inference"]["infer_on_import"] is False
    r = _upload(api, [("a.yaml", example("light-spatial-domains.yaml").encode())])
    assert r.json()["infer"] is False
    _run_all()
    assert api.get("/import/active").json()["active"]["status"] == "done"
    assert calls == []  # the switch is honoured: template cards, questions queued
    session.rollback()  # the job wrote in its own session: read past this session's snapshot
    origins = {c.origin for c in session.query(ReviewCard).all()}
    assert "local_llm" not in origins  # whatever cards exist came from the export or a template
    # a form field overrides the setting for one batch
    api.delete(f"/import/{r.json()['batch']}")
    r = _upload(api, [("b.yaml", example("deep-grn-atlas.yaml").encode())], infer="1")
    assert r.json()["infer"] is True
    _run_all()
    assert any(c[0] == "card" for c in calls)
    # cached answers are served even when inference is off
    session.rollback()
    cached = session.query(KV).filter(KV.k.like("inf:%")).count()
    assert cached > 0
    n = len(calls)
    token = cache.INFER_ENABLED.set(False)
    try:
        assert cache.judged(session, "make_card", "something new") is None  # not cached: no call
        assert len(calls) == n
    finally:
        cache.INFER_ENABLED.reset(token)
    reset_backend()
