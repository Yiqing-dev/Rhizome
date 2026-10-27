# SPDX-License-Identifier: Apache-2.0
"""Reading a new paper with the API: identify, stop at a paper the library has, review, export RXF,
validate (one repair round), ingest, keep the report. Scripted client; nothing leaves the machine."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("anthropic")

from rhizome.db.models import Extraction  # noqa: E402
from rhizome.inference import reader  # noqa: E402
from rhizome.inference.reader import ReaderError, find_existing, read_paper, slug_of  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[2] / "rxf-spec" / "examples"
PDF = b"%PDF-1.4\n% a fake paper\n"


class Client:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        nxt = self.answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        stop = "end_turn"
        if isinstance(nxt, tuple):
            stop, nxt = nxt
        text = nxt if isinstance(nxt, str) else json.dumps(nxt)
        return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=10, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=50))


IDENT = {"doi": "10.5555/rhz.example.0004", "arxiv_id": None, "title": "New paper", "year": 2026,
         "first_author": "Lin", "venue": None}


def test_full_read_ingests_and_keeps_the_report(settings, session):
    yaml = (EXAMPLES / "deep-with-ids.yaml").read_text("utf-8")
    c = Client([IDENT, "# Review\n\nSolid work.", "```yaml\n" + yaml + "```"])
    steps = []
    out = read_paper(session, PDF, "Lin 2026 duckweed.pdf", client=c, model="claude-opus-5-5", lang="en",
                     progress=steps.append)
    assert out["status"] == "done" and out["work"]["key"] and out["ingest"]["ok"]
    assert steps == ["identify", "dedupe", "review", "export", "validate", "ingest"]
    assert out["usage"] == {"calls": 3, "input": 300, "output": 30, "cache_read": 0, "cache_write": 150}
    # the PDF went once, cached, in the first user turn; the system prompt carries the instructions and vocabulary
    first = c.calls[0]
    doc = first["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf" and doc["cache_control"]
    assert "instructions_version" in first["system"][1]["text"] and "topics:" in first["system"][1]["text"]
    assert first["output_config"]["format"]["type"] == "json_schema" and first["output_config"]["effort"] == "low"
    assert all(call["fallbacks"] == "default" for call in c.calls)
    # the conversation is append-only and the export turn names the version and the date
    assert [m["role"] for m in c.calls[2]["messages"]] == ["user", "assistant", "user", "assistant", "user"]
    assert "rhizome-reader-v1" in c.calls[2]["messages"][-1]["content"]
    # the report sits beside the export and the card shows it
    ex = session.execute(select(Extraction).where(Extraction.work_key == out["work"]["key"], Extraction.kind == "rxf")).scalars().first()
    assert ex.meta["report_sha"] and ex.meta["reader"]["model"] == "claude-opus-5-5"
    from rhizome.services.views import entity_card

    card = entity_card(session, out["work"]["id"], touch_access=False)
    assert card["exports"][0]["report"].startswith("# Review") and card["exports"][0]["has_pdf"]


def test_a_paper_the_library_has_costs_one_small_request(settings, library, session):
    c = Client([{**IDENT, "doi": "10.5555/rhz.example.0003", "title": "something else"}])
    out = read_paper(session, PDF, "again.pdf", client=c, lang="en")
    assert out["status"] == "duplicate" and out["work"]["key"] and len(c.calls) == 1
    # by title and by arXiv id too
    title = out["work"]["name"]
    assert find_existing(session, {"title": title.upper()}) is not None
    assert find_existing(session, {"doi": None, "arxiv_id": "9999.00001", "title": "nope"}) is None
    # force reads it anyway
    yaml = (EXAMPLES / "light-spatial-domains.yaml").read_text("utf-8")
    c = Client([{**IDENT, "doi": "10.5555/rhz.example.0003"}, "report", yaml])
    out = read_paper(session, PDF, "again.pdf", client=c, lang="en", force=True)
    assert out["status"] == "done" and len(c.calls) == 3


def test_invalid_export_gets_one_repair_round(settings, session):
    good = (EXAMPLES / "light-spatial-domains.yaml").read_text("utf-8")
    c = Client([IDENT, "report", "rxf_version: 1\npaper: {year: 2020}\n", "```yaml\n" + good + "\n```"])
    out = read_paper(session, PDF, "p.pdf", client=c, lang="en")
    assert out["status"] == "done" and len(c.calls) == 4
    assert "paper" in c.calls[3]["messages"][-1]["content"]  # the validator's report went back
    c = Client([IDENT, "report", "rxf_version: 1\n", "rxf_version: 1\n"])
    out = read_paper(session, PDF, "p.pdf", client=c, lang="en")
    assert out["status"] == "invalid" and out["errors"] and out["report"] == "report" and len(c.calls) == 4


def test_api_failures_and_bad_input_are_errors(settings, session):
    import httpx2 as httpx

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    import anthropic

    c = Client([anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)])
    with pytest.raises(ReaderError, match="AuthenticationError"):
        read_paper(session, PDF, "p.pdf", client=c, lang="en")
    with pytest.raises(ReaderError, match="not a PDF"):
        read_paper(session, b"hello", "p.pdf", client=Client([]), lang="en")
    c = Client([IDENT, ("max_tokens", "half a rev")])
    with pytest.raises(ReaderError, match="truncated"):
        read_paper(session, PDF, "p.pdf", client=c, lang="en")
    assert slug_of("My Paper (2026) final!.pdf") == "My-Paper-2026-final"


def test_read_routes_stage_pdfs_and_queue_jobs(settings, session, monkeypatch, tmp_path):
    from rhizome.api.app import create_app
    from rhizome.db.models import Job

    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    assert c.get("/read/status").json() == {"enabled": False, "reason": "backend"}
    assert c.get("/stats").json()["reader"]["reason"] == "backend"
    r = c.post("/read", files=[("files", ("a.pdf", PDF, "application/pdf"))])
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "backend"

    monkeypatch.setattr(reader, "availability", lambda: {"enabled": True, "reason": None, "model": "m"})
    r = c.post("/read", files=[("files", ("a.pdf", PDF, "application/pdf")), ("files", ("a.pdf", PDF, "application/pdf")),
                               ("files", ("notes.txt", b"hi", "text/plain"))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["jobs"]) == 2 and body["skipped"] == ["notes.txt"]
    paths = [Path(j["payload"]["path"]) for j in body["jobs"]]
    assert all(p.exists() and p.read_bytes() == PDF for p in paths) and paths[0] != paths[1]
    assert all(j["kind"] == "read_paper" and j["status"] == "queued" for j in body["jobs"])
    assert c.post("/read", files=[("files", ("x.txt", b"no", "text/plain"))]).status_code == 422

    # running the job: the handler reads the staged file, reports progress, and removes it when done
    from rhizome import jobs

    def fake_read(s, pdf, name, *, force=False, progress=None, **kw):
        assert pdf == PDF and name == "a.pdf"
        progress("review")
        return {"status": "done", "work": {"id": 1, "key": "work:x"}, "paper": {"title": "T"}, "usage": {}}

    monkeypatch.setattr(reader, "read_paper", fake_read)
    jid = body["jobs"][0]["id"]
    assert jobs.run_job(jid).status == "done"
    with jobs.session_scope() as s2:
        j = s2.get(Job, jid)
        assert j.result["status"] == "done" and jobs.progress_of(s2, jid)["step"] == "review"
    assert not paths[0].exists() and paths[1].exists()
    # a duplicate keeps its file so "read anyway" can re-queue it
    fj = c.post(f"/read/{body['jobs'][1]['id']}/force").json()
    assert fj["payload"]["force"] is True and fj["payload"]["path"] == str(paths[1])
    assert c.post(f"/read/{jid}/force").status_code == 409  # its file is gone
