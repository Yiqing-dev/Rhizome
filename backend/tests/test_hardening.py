# SPDX-License-Identifier: Apache-2.0
"""Small holes closed: command-line fields are validated, secrets stay out of logs and dumps,
/health says nothing to strangers, repeats are idempotent, inputs are guarded, and a few report
and card details."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import example
from rhizome.db.models import HumanDecision, ReviewCard
from rhizome.pipeline import decisions
from rhizome.pipeline.graph import Graph
from rhizome.pipeline.ingest import ingest_text


@pytest.fixture()
def api(settings, library, session):
    from rhizome.api.app import create_app

    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    return c


def test_remote_fields_cannot_smuggle_options():
    from rhizome.config import RemoteTarget
    from rhizome.services.snapshot import sync_command

    for bad in ({"host": "-oProxyCommand=evil"}, {"host": "a b"}, {"path": "-x"}, {"ssh_options": ["-o", "ProxyCommand=x"]},
                {"ssh_options": ["-o ProxyCommand=x"]}, {"control_path": "a\nb"}):
        with pytest.raises(ValueError):
            RemoteTarget(name="r", **{"host": "me@h", **bad})
    cmd = sync_command(RemoteTarget(name="r", host="me@h", ssh_options=["-p2222"]))
    assert cmd[cmd.index("--") + 1] == "me@h" and "-p2222" in cmd


def test_secrets_are_redacted(settings, api, tmp_path):
    from rhizome.config import redact_url

    assert redact_url("postgresql+psycopg://u:hunter2@db/x") == "postgresql+psycopg://u:***@db/x"
    assert redact_url(None) is None and redact_url("::nonsense") in ("<redacted>", "::nonsense")
    assert "hunter2" not in json.dumps(api.get("/settings").json())
    r = api.patch("/settings", json={"remotes": [{"name": "x", "host": "me@h"}]})
    assert r.status_code == 422 and "settings.json" in r.json()["detail"]


def test_health_auth_and_host_checks(settings, api):
    anon = TestClient(api.app)
    h = anon.get("/health").json()
    assert h["ok"] and h["app"] == "rhizome" and "version" not in h and "language" not in h
    full = api.get("/health").json()
    assert full["version"] and full["language"] in ("en", "zh_CN")
    # a non-ASCII bearer value (HTTP clients cannot even send one; at the ASGI level it arrives
    # latin-1 decoded) is a 401, not a 500 from compare_digest
    import anyio

    async def call() -> int:
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": "/stats", "raw_path": b"/stats", "query_string": b"", "client": ("127.0.0.1", 1),
                 "server": ("127.0.0.1", 80),
                 "headers": [(b"host", b"testserver"), (b"authorization", "Bearer t\u00f6ken".encode("latin-1"))]}
        status: list[int] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            if msg["type"] == "http.response.start":
                status.append(msg["status"])
        await api.app(scope, receive, send)
        return status[0]

    assert anyio.run(call) == 401
    api.app.state.enforce_host = True  # as `rhz serve` sets it
    try:
        assert anon.get("/health", headers={"host": "evil.example.com"}).status_code == 421
        assert anon.get("/health", headers={"host": "localhost:8765"}).status_code == 200
        assert anon.get("/health", headers={"host": "127.0.0.1:1"}).status_code == 200
    finally:
        api.app.state.enforce_host = False
    assert api.get("/").headers.get("cache-control") == "no-cache"


def test_stale_server_marker_is_ignored(settings, monkeypatch):
    from rhizome import system

    system.write_server_marker("http://127.0.0.1:1")
    assert system.running_server_url() == "http://127.0.0.1:1"
    p = settings.data_dir / system.SERVER_FILE
    p.write_text(json.dumps({"url": "http://127.0.0.1:1", "pid": 2_147_000_000}), "utf-8")
    assert system.running_server_url() is None


def test_decisions_are_idempotent_and_validated(library, session, api):
    g = Graph(session)
    n0 = session.execute(select(HumanDecision)).scalars().all()
    d1 = decisions.record(g, "add_alias", {"key": "topic:grn inference", "alias": "GRN inf."})
    d2 = decisions.record(g, "add_alias", {"key": "topic:grn inference", "alias": "GRN inf."})
    assert d1.id == d2.id and len(session.execute(select(HumanDecision)).scalars().all()) == len(n0) + 1
    with pytest.raises(decisions.DecisionError, match="must not be empty"):
        decisions.record(g, "rename", {"key": "topic:grn inference", "name": "   "})
    session.commit()
    assert api.post("/topic", json={"name": "  "}).status_code == 422
    r = api.get("/decisions", params={"limit": 2})
    assert r.status_code == 200 and "total" in r.json() and len(r.json()["decisions"]) <= 2
    assert api.get("/decisions", params={"limit": 5000}).status_code == 422


def test_resent_export_attaches_its_pdf_and_bad_inputs_are_refused(settings, session):
    doc = example("deep-grn-atlas.yaml")
    assert ingest_text(session, doc, "a.yaml").ok
    r = ingest_text(session, doc, "a.yaml", pdf=b"%PDF-1.7 synthetic")
    assert r.duplicate and r.pdf_attached is True
    from rhizome.db.models import Extraction

    ex = session.execute(select(Extraction).where(Extraction.kind == "rxf")).scalars().first()
    assert len(ex.input_hashes) == 2
    again = ingest_text(session, doc, "a.yaml", pdf=b"%PDF-1.7 synthetic")
    assert again.pdf_attached is False
    bad = ingest_text(session, example("light-scenic-benchmark.yaml"), "b.yaml", pdf=b"GIF89a not a pdf")
    assert not bad.ok and "PDF" in bad.report


def test_required_fields_are_counted_once_and_quoted_versions_repaired():
    from rhizome.rxf.loader import load_rxf, report_for

    doc = example("light-scenic-benchmark.yaml")
    two = doc.replace("{name: benchmarking, relation: about}", "{name: benchmarking}").replace(
        "{name: gene regulatory network inference, relation: about}", "{name: gene regulatory network inference}")
    r = load_rxf(two)
    assert len(r.problems) == 2 and "2 problems from 1 causes" in report_for("x.yaml", r)
    quoted = load_rxf(doc.replace("rxf_version: 1", 'rxf_version: "1"'))
    assert quoted.ok and quoted.repairs == ["rxf_version_type"]
    assert load_rxf(doc.replace("rxf_version: 1", "rxf_version: 1.0")).ok
    bad = load_rxf(doc.replace("rxf_version: 1", "rxf_version: '1x'"))
    assert not bad.ok and "'1x'" in bad.problems[0].message


def test_malformed_openalex_payload_is_transient(monkeypatch, settings):
    from rhizome.config import set_settings
    from rhizome.external import openalex

    set_settings(settings.model_copy(update={"offline": False}))

    class R:
        status_code = 200

        def json(self):
            return {"id": "https://openalex.org/W1", "authorships": "oops", "referenced_works": None}

    monkeypatch.setattr("rhizome.external.verify.get", lambda *a, **k: R())
    rec, status = openalex.fetch(doi="10.1000/x")
    assert status == "ok" and rec["authors"] == [] and rec["referenced_works"] == []

    class Boom(R):
        def json(self):
            return {"no_id": True}

    monkeypatch.setattr("rhizome.external.verify.get", lambda *a, **k: Boom())
    assert openalex.fetch(doi="10.1000/x") == (None, "transient")


def test_review_day_rolls_over_at_four_and_count_is_capped(library, session, settings):
    from datetime import datetime, timezone

    from rhizome.services.cards import ROLLOVER_HOUR, _day_start, due_count

    local_offset = datetime.now().astimezone().utcoffset()
    one_am_local = (datetime(2026, 1, 10, 1, 0).replace(tzinfo=None) - local_offset)
    start = _day_start(one_am_local)
    assert (start + local_offset).hour == ROLLOVER_HOUR and start < one_am_local
    assert (one_am_local - start).total_seconds() == 21 * 3600  # yesterday 04:00
    settings.review_daily_new = 2
    assert due_count(session) <= 2
    assert datetime.now(timezone.utc)  # keep the import honest


def test_light_export_does_not_overwrite_deep_summary_and_cards_skip_dropped_items(settings, session):
    deep = example("deep-grn-atlas.yaml")
    assert ingest_text(session, deep, "deep.yaml").ok
    g = Graph(session)
    w = g.by_key("work:doi:10.5555/rhz.example.0001")
    tldr = list(w.attrs["tldr"])
    light = deep.replace("depth: deep", "depth: light").replace("  - Built a 120k", "  - A later light note about a 120k")
    light = light.split("issues:")[0]  # a light export has no issues / insights / cards sections
    assert ingest_text(session, light, "light.yaml").ok
    assert g.by_key("work:doi:10.5555/rhz.example.0001").attrs["tldr"] == tldr
    assert g.by_key("work:doi:10.5555/rhz.example.0001").attrs["depth"] == "deep"
    # a review card about an accession that was dropped as fabricated attaches to nothing
    doc = example("deep-with-ids.yaml")
    from rhizome.pipeline import ingest as ing

    monkeypatch_checks = {"CNP9990004": "not_found"}
    real = ing._check_ids

    def fake(d, s=None):
        checks, suspect = real(d, s)
        checks.update(monkeypatch_checks)
        return checks, suspect + ["CNP9990004"]

    ing._check_ids = fake
    try:
        doc = doc.replace("review_cards:", "review_cards:\n  - {id: r9, q: 'What is in d1?', a: leaf cells, about: d1}")
        r = ingest_text(session, doc, "ids.yaml")
        assert r.ok, r.report
    finally:
        ing._check_ids = real
    cards = [c.q for c in session.execute(select(ReviewCard)).scalars()]
    assert "What is in d1?" not in cards
    claim = next(e for e in session.execute(select(g.by_id(w.id).__class__)).scalars() if e.type == "claim")
    assert "evidence_type" not in (claim.attrs or {})


def test_mcp_tools_carry_annotations_and_ingest_repairs(settings, session):
    import asyncio

    import rhizome.mcp_server as m
    from rhizome.client import LocalClient

    tools = asyncio.run(m.mcp.list_tools())
    by = {t.name: t for t in tools}
    assert by["rhz_search"].annotations.readOnlyHint is True
    assert by["rhz_decide"].annotations.destructiveHint is True and by["rhz_undo"].annotations.destructiveHint is True
    assert by["rhz_ingest"].annotations.readOnlyHint is False
    m._client = LocalClient(settings)
    drift = (settings.data_dir.parent / "drift.yaml")
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "drift-repairable.yaml"
    out = json.loads(m.rhz_ingest(fixture.read_text("utf-8")))
    assert out["ok"] is False and out["repairable"] and "repair=true" in out["hint"]
    out = json.loads(m.rhz_ingest(fixture.read_text("utf-8"), repair=True))
    assert out["ok"] and out["repairs"]
    assert not drift.exists()


def test_app_built_like_rhz_serve_answers_health(settings):
    """`rhz serve` builds the app without passing settings; the Host check and the index preload
    must use the resolved settings, not the (None) argument (the 0.1.5 Windows smoke caught it)."""
    from rhizome.api.app import create_app

    app = create_app(start_worker=False)
    app.state.enforce_host = True
    with TestClient(app) as c:  # with lifespan: the preload thread starts as well
        r = c.get("/health", headers={"host": "127.0.0.1:51999"})
        assert r.status_code == 200 and r.json()["ok"]
        assert c.get("/health", headers={"host": "evil.example.com"}).status_code == 421
    import threading

    for t in threading.enumerate():  # the preload must not outlive this test's database (segfault on 3.10)
        if t.name == "rhizome-preload":
            t.join(30)
