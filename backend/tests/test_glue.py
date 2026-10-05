# SPDX-License-Identifier: Apache-2.0
"""The glue the unit tests used to skip: the worker's loop step, the app's lifespan, the inbox
event handler on a Chinese path, the HTTP client against a real server (same answers as the
in-process client), two processes writing at once, and file encodings."""

import multiprocessing as mp
import os
import socket
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import example
from rhizome.db.models import Job


# ---- T3 ---------------------------------------------------------------------------------------

def test_worker_step_runs_jobs_backups_and_the_nightly_check(library, session, settings, monkeypatch):
    from rhizome import jobs

    session.commit()
    w = jobs.Worker(interval=0.01)
    w.startup()
    assert any(p.name.startswith("rhizome-daily") for p in settings.backups_dir.iterdir())
    jobs.enqueue(session, "enrich", {})
    session.commit()
    assert w.step(now=1000.0) is True  # ran the job (the first step also queued the nightly batch)
    session.expire_all()
    assert session.execute(select(Job).where(Job.kind == "enrich")).scalars().first().status == "done"
    while w.step(now=1000.0):
        pass  # drain what the nightly check queued
    checks = []
    monkeypatch.setattr(jobs, "maybe_schedule_nightly", lambda s: checks.append(1))
    assert w.step(now=1000.0 + jobs.CHECK_EVERY + 1) is False  # wall clock passed: the check ran, no job
    assert w.step(now=1000.0 + jobs.CHECK_EVERY + 2) is False  # not again within the window
    assert len(checks) == 1


def test_app_lifespan_starts_and_stops_the_worker(settings):
    from fastapi.testclient import TestClient

    from rhizome.api.app import create_app

    app = create_app(settings, start_worker=True, watch_inbox=True)
    with TestClient(app) as c:
        assert c.get("/health").json()["ok"]
        names = {t.name for t in threading.enumerate()}
        assert "rhizome-worker" in names and "rhizome-inbox" in names
        worker = next(t for t in threading.enumerate() if t.name == "rhizome-worker")
    worker.join(timeout=10)
    assert not worker.is_alive()


def test_inbox_handler_processes_a_chinese_path(settings):
    from rhizome.inbox import _Handler

    class Ev:
        is_directory = False

        def __init__(self, p):
            self.src_path = str(p)
            self.dest_path = str(p)

    p = settings.inbox / "空间 结构域 导出.yaml"
    p.write_text(example("light-spatial-domains.yaml"), "utf-8")
    seen = []
    h = _Handler(lambda path, res: seen.append((path.name, res.ok)))
    h.on_created(Ev(p))
    assert seen == [("空间 结构域 导出.yaml", True)]
    assert (settings.inbox / "done" / "空间 结构域 导出.yaml").exists()
    outside = settings.data_dir / "elsewhere.yaml"
    outside.write_text("x", "utf-8")
    h.on_moved(Ev(outside))  # not in the inbox: ignored
    assert len(seen) == 1


# ---- T5 ---------------------------------------------------------------------------------------

@pytest.fixture()
def server(settings, library, session):
    """The app on a real port, as the desktop runs it, so HttpClient is tested for real."""
    import uvicorn

    from rhizome.api.app import create_app

    session.commit()
    app = create_app(settings, start_worker=False, watch_inbox=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    deadline = time.time() + 15
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started
    yield f"http://127.0.0.1:{port}", app.state.token
    srv.should_exit = True
    th.join(timeout=10)


def _clients(server, settings):
    from rhizome.client import HttpClient, LocalClient

    base, token = server
    return {"local": LocalClient(settings), "http": HttpClient(base, token)}


def test_clients_agree_including_on_errors(server, settings):
    clients = _clients(server, settings)
    answers = {}
    for name, c in clients.items():
        hits = c.search("DomainGAT", types="method", limit=500)  # over the cap: clamped, not an error
        card = c.get_by_key("method:domaingat") or c.get(hits[0]["id"])
        answers[name] = {
            "top": hits[0]["name"], "card": card["name"],
            "missing_key": c.get_by_key("method:does-not-exist"),
            "missing_id": c.get(99_999_999),
            "related_missing": c.related(99_999_999),
            "bad_action": c.resolve(99_999_999, "merge"),
            "bad_decide": c.decide("rename", {"key": "method:nope", "name": "x"}),
            "queue": c.queue(None, 5)["total"] >= 0,
            "recall": [h["name"] for h in c.recall("spatial domain detection on Stereo-seq", 3)][:1],
            "undo_missing": c.revoke(99_999_999).get("ok"),
            "data": (c.data("GSE999001") or {}).get("name"),
            "data_missing": c.data("GSE000"),
        }
    assert answers["local"]["top"] == answers["http"]["top"] == "DomainGAT"
    assert answers["local"] == answers["http"], answers
    assert answers["local"]["bad_action"]["ok"] is False and answers["local"]["bad_decide"]["ok"] is False
    assert answers["local"]["missing_key"] is None and answers["local"]["undo_missing"] is False


def test_mcp_tools_answer_the_same_either_way(server, settings, monkeypatch):
    import rhizome.mcp_server as m

    out = {}
    for name, c in _clients(server, settings).items():
        m._client = c
        out[name] = {
            "decide": m.rhz_decide(99_999_999, "merge"),
            "correct": m.rhz_correct("rename", {"key": "method:nope", "name": "x"}),
            "undo": m.rhz_undo(99_999_999),
            "get": m.rhz_get("method:nope"),
            "related": m.rhz_related("99999999"),
        }
    assert out["local"] == out["http"], out
    assert '"ok": false' in out["http"]["decide"] and '"ok": false' in out["http"]["correct"]


# ---- T11 --------------------------------------------------------------------------------------

def _writer(data_dir: str, doc: str, tag: str, n: int, q):
    """A second process ingesting papers in a loop (CLI next to the app, Claude next to the CLI)."""
    os.environ["RHIZOME_OFFLINE"] = "1"
    os.environ["RHIZOME_DATA_DIR"] = data_dir
    from rhizome.client import LocalClient
    from rhizome.config import Settings

    c = LocalClient(Settings(data_dir=Path(data_dir), offline=True, language="en"))
    errors = []
    for i in range(n):
        text = doc.replace("doi: 10.5555/rhz.example.000", f"doi: 10.5555/{tag}.{i}.").replace(
            "title: ", f"title: {tag} {i} ")
        try:
            r = c.ingest(text, f"{tag}-{i}.yaml")
            if not r.get("ok"):
                errors.append(r.get("report", "?")[:200])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}"[:200])
    q.put(errors)


def test_two_processes_ingest_concurrently(settings):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    docs = [(example("light-scenic-benchmark.yaml"), "a"), (example("light-spatial-domains.yaml"), "b")]
    procs = [ctx.Process(target=_writer, args=(str(settings.data_dir), d, tag, 6, q)) for d, tag in docs]
    for p in procs:
        p.start()
    results = [q.get(timeout=240) for _ in procs]
    for p in procs:
        p.join(timeout=30)
    assert all(not errs for errs in results), results
    from rhizome.client import LocalClient

    hits = LocalClient(settings).search("benchmark", types="work", limit=50)
    assert len(hits) >= 6


@pytest.mark.bench
@pytest.mark.skipif(not os.environ.get("RHIZOME_BENCH"), reason="RHIZOME_BENCH=1 to run")
def test_bench_synthetic_library(settings, session):
    """Timings at ~2k works (printed, never asserted): search, a paper card, a two-hop graph."""
    from rhizome.pipeline.graph import Graph
    from rhizome.services.search import Filters, search
    from rhizome.services.views import entity_card, neighbors

    g = Graph(session)
    topic = g.create("topic", "topic:bench", "bench topic")
    for i in range(2000):
        w = g.create("work", f"work:title:bench{i}", f"Benchmark paper {i} on spatial domains and regulatory networks")
        g.upsert_edge(w, topic, "about")
        if i % 10 == 0:
            g.upsert_edge(w, g.create("method", f"method:bench{i}", f"Method {i}"), "proposes")
    session.commit()
    for label, fn in (("search", lambda: search(session, "spatial domains", Filters())),
                      ("card", lambda: entity_card(session, topic.id)),
                      ("neighbors2", lambda: neighbors(session, topic.id, hops=2))):
        t0 = time.perf_counter()
        fn()
        print(f"{label}: {(time.perf_counter() - t0) * 1000:.0f} ms")


# ---- F9 ---------------------------------------------------------------------------------------

def test_non_utf8_exports_get_a_plain_message(settings, session, tmp_path):
    from rhizome.inbox import looks_like_rxf
    from rhizome.pipeline.ingest import ingest_file
    from rhizome.rxf.loader import RxfEncodingError, decode_rxf

    body = example("light-spatial-domains.yaml")
    assert decode_rxf(body.encode("utf-8-sig")) == body
    assert decode_rxf(body.encode("utf-16")) == body
    assert decode_rxf(("# 注释 中文 中文 中文\n" + body).encode("gb18030")).endswith(body)
    with pytest.raises(RxfEncodingError) as e:
        decode_rxf(b"rxf_version: 1\npaper:\n  title: caf\xe9\n")
    assert "UTF-8" in str(e.value)
    f = settings.inbox / "utf16.yaml"
    f.write_bytes(body.encode("utf-16"))
    assert looks_like_rxf(f)
    assert ingest_file(f).ok
    bad = settings.inbox / "latin1.yaml"
    bad.write_bytes(b"rxf_version: 1\npaper:\n  title: caf\xe9\n")
    r = ingest_file(bad)
    assert not r.ok and "UTF-8" in r.report
    report = (settings.inbox / "error" / "latin1.yaml.error.txt").read_text("utf-8")
    assert "UTF-8" in report and "Traceback" not in report
    from fastapi.testclient import TestClient

    from rhizome.api.app import create_app

    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    r = c.post("/ingest", files={"file": ("x.yaml", b"rxf_version: 1\npaper:\n  title: caf\xe9\n")})
    assert r.status_code == 422 and "UTF-8" in r.json()["detail"]["report"]


# ---- F5 ---------------------------------------------------------------------------------------

def test_search_pages_and_browse_filters_in_sql(library, session):
    from rhizome.services.search import Filters, search_page

    first = search_page(session, "", Filters(types=("method",)), limit=2, offset=0)
    assert first["total"] >= 3 and first["has_more"] and len(first["results"]) == 2
    rest = search_page(session, "", Filters(types=("method",)), limit=50, offset=2)
    assert not rest["has_more"] and len(rest["results"]) == first["total"] - 2
    assert {h["id"] for h in first["results"]}.isdisjoint({h["id"] for h in rest["results"]})
    at = search_page(session, "", Filters(types=("dataset", "method"), organism="Arabidopsis thaliana"))
    assert at["total"] >= 1 and all(any(s["title"] for s in h["sources"]) for h in at["results"])
    yr = search_page(session, "", Filters(types=("work",), year_min=2030))
    assert yr["total"] == 0 and yr["results"] == []
    role = search_page(session, "", Filters(types=("method",), edge_type="proposes"))
    assert role["total"] >= 1 and all(any(s["edge"] == "proposes" for s in h["sources"]) for h in role["results"])
    text = search_page(session, "network inference", Filters(), limit=1)
    assert len(text["results"]) == 1 and text["has_more"] is True and "total" not in text
