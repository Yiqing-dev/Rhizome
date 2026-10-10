# SPDX-License-Identifier: Apache-2.0
"""The topic agent reads the library through tools before suggesting how two topics relate; its
verdicts survive rebuilds without new API calls, and applying them makes ordinary decisions.
Generative judgements are remembered so a rebuild never asks the backend twice."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import example
from rhizome.db.models import HumanDecision, ReviewItem
from rhizome.inference.agent import ACTIONS, LibraryTools, TopicAgent, cached_verdict, review_with_agent
from rhizome.pipeline.graph import Graph


def _tool_use(id_, name, **inp):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)


def _text(obj):
    return SimpleNamespace(type="text", text=json.dumps(obj))


class ScriptedMessages:
    """Plays back responses; records every request so the test can inspect the conversation."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        stop, content = self.responses.pop(0)
        return SimpleNamespace(stop_reason=stop, content=content)


def scripted_agent(*responses):
    msgs = ScriptedMessages(*responses)
    return TopicAgent("claude-opus-5-5", client=SimpleNamespace(beta=SimpleNamespace(messages=msgs))), msgs


def _pair(session):
    g = Graph(session)
    a, b = g.by_alias("topic", "GRN inference"), g.by_alias("topic", "gene regulatory network inference")
    assert a is not None and b is not None and a.id != b.id
    return g, a, b


def _queue_pair(g, a, b, kind="topic_relation"):
    return g.queue(kind, {"a": a.key, "b": b.key, "a_name": a.canonical_name, "b_name": b.canonical_name},
                   dedupe=f"{kind}:{a.key}|{b.key}", score=0.7)


def test_library_tools_describe_topics(library, session):
    g, a, b = _pair(session)
    tools = LibraryTools(session)
    info = json.loads(tools.topic_info(a.key))
    assert info["name"] == "GRN inference" and info["papers"] and info["tagged_counts"].get("work", 0) >= 1
    assert json.loads(tools.topic_info("topic:nothing like this"))["error"]
    shared = json.loads(tools.shared_material(a.key, b.key))
    assert "shared_papers" in shared and shared["existing_is_a"] == []
    assert any(h["name"] == "GRN inference" for h in json.loads(tools.search_topics("GRN")))
    out, err = tools.run("delete_everything", {})
    assert err and "unknown tool" in out


def test_agent_looks_up_then_answers(library, session):
    g, a, b = _pair(session)
    agent, msgs = scripted_agent(
        ("tool_use", [_tool_use("t1", "topic_info", key=a.key), _tool_use("t2", "shared_material", a=a.key, b=b.key)]),
        ("end_turn", [_text({"action": "merge", "confidence": 0.93,
                             "reason": "GRN is the acronym of gene regulatory network; both tag the same papers."})]),
    )
    v = agent.judge(LibraryTools(session), "topic_relation", {"a": a.key, "b": b.key, "a_name": a.canonical_name,
                                                               "b_name": b.canonical_name})
    assert v["action"] == "merge" and v["confidence"] == 0.93 and v["turns"] == 2
    first, second = msgs.calls
    assert first["output_config"]["format"]["schema"]["properties"]["action"]["enum"] == list(ACTIONS["topic_relation"])
    assert first["output_config"]["effort"] == "medium" and first["fallbacks"] == "default"
    assert first["cache_control"] == {"type": "ephemeral"} and all(t["strict"] for t in first["tools"])
    assert "tool_choice" not in first and "thinking" not in first
    # the assistant turn goes back unchanged, then one user turn with every tool result
    assert second["messages"][1]["content"][0].name == "topic_info"
    results = second["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and json.loads(results[0]["content"])["name"] == "GRN inference"


def test_agent_answers_are_validated(library, session):
    _, a, b = _pair(session)
    p = {"a": a.key, "b": b.key, "a_name": a.canonical_name, "b_name": b.canonical_name}
    agent, _ = scripted_agent(("end_turn", [_text({"action": "a_is_a_b", "confidence": 0.9, "reason": "x"})]))
    assert agent.judge(LibraryTools(session), "merge", p)["error"]  # not an action of a merge item
    agent, _ = scripted_agent(("refusal", []))
    assert agent.judge(LibraryTools(session), "merge", p) == {"error": "refusal"}
    looping = [("tool_use", [_tool_use(f"t{i}", "search_topics", query="GRN")]) for i in range(6)]
    agent, msgs = scripted_agent(*looping)
    assert agent.judge(LibraryTools(session), "merge", p)["error"] and len(msgs.calls) == 6
    assert msgs.calls[-1]["messages"][-1]["content"][-1]["text"].startswith("No more lookups")


def test_verdicts_are_stored_survive_regeneration_and_apply(settings, library, session):
    g, a, b = _pair(session)
    it = _queue_pair(g, a, b)
    session.commit()
    agent, msgs = scripted_agent(("end_turn", [_text({"action": "merge", "confidence": 0.95, "reason": "same topic"})]))
    out = review_with_agent(session, agent=agent, item_ids=[it.id])
    assert out["judged"] == 1 and out["remaining"] == 0 and len(msgs.calls) == 1
    session.refresh(it)
    assert it.payload["agent"]["action"] == "merge" and cached_verdict(session, it.dedupe_key)["confidence"] == 0.95
    # a second run does not ask again
    assert review_with_agent(session, agent=scripted_agent()[0], item_ids=[it.id])["judged"] == 0
    # a rebuild deletes and regenerates pending items: the verdict comes back without a call
    dedupe = it.dedupe_key
    session.delete(it)
    session.flush()
    again = _queue_pair(Graph(session), a, b)
    assert again.dedupe_key == dedupe and again.payload["agent"]["reason"] == "same topic"
    session.commit()
    # apply: an ordinary, undoable decision
    from rhizome.services.review import agent_summary, apply_agent

    settings.inference_backend = "anthropic"
    try:
        summary = agent_summary(session)
        assert summary["enabled"] and summary["applicable"] == 1 and summary["min_confidence"] == 0.8
        assert apply_agent(session, 0.99)["applied"] == 0
        r = apply_agent(session, 0.8)
        assert r == {"applied": 1, "by_action": {"merge": 1}}
        d = session.execute(select(HumanDecision).order_by(HumanDecision.id.desc())).scalars().first()
        assert d.op == "merge" and {d.payload["from"], d.payload["into"]} == {a.key, b.key}
    finally:
        settings.inference_backend = "queue"


def test_run_is_skipped_without_the_backend_and_stops_on_api_errors(settings, library, session):
    anthropic = pytest.importorskip("anthropic")
    import httpx2

    assert review_with_agent(session)["skipped"]
    g, a, b = _pair(session)
    target = _queue_pair(g, a, b, kind="merge")
    session.commit()

    class Boom:
        def create(self, **kw):
            req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=req), body=None)

    agent = TopicAgent("claude-opus-5-5", client=SimpleNamespace(beta=SimpleNamespace(messages=Boom())))
    out = review_with_agent(session, agent=agent, item_ids=[target.id])
    assert out["stopped"].startswith("RateLimitError") and out["judged"] == 0 and out["remaining"] == 1
    it = session.get(ReviewItem, target.id)
    assert "agent" not in it.payload  # nothing paid for, nothing stored: the next run tries again


def test_review_api_reports_and_applies(settings, library, session):
    from rhizome.api.app import create_app

    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    assert c.get("/review").json()["agent"] == {"enabled": False}
    c.patch("/settings", json={"inference_backend": "anthropic"})
    try:
        assert c.get("/review").json()["agent"]["enabled"] is True
        r = c.post("/review/agent/apply", json={"min_confidence": 0.9})  # a literal path, not an item id
        assert r.status_code == 200 and r.json()["applied"] == 0
        assert c.post("/review/agent/apply", json={"min_confidence": 0.2}).status_code == 422
        j = c.post("/jobs", json={"kind": "agent_review", "payload": {}}).json()
        assert j["kind"] == "agent_review"
    finally:
        c.patch("/settings", json={"inference_backend": "queue"})


class CountingBackend:
    name = "counting"
    model = "test"

    def __init__(self):
        self.calls = 0

    def classify_topic_relation(self, asset_text, topic_def):
        self.calls += 1
        return ("about", 0.5)

    def judge_breadth(self, a, b):
        self.calls += 1
        return ("none", 0.9)

    def make_card(self, asset_text):
        self.calls += 1
        return ("Q about " + asset_text[:20], "A")


def test_rebuild_does_not_ask_the_backend_again(settings, session, monkeypatch):
    import rhizome.inference as inference
    from rhizome.pipeline.ingest import ingest_text
    from rhizome.pipeline.rebuild import rebuild

    fake = CountingBackend()
    monkeypatch.setattr(inference, "_backend", fake)
    monkeypatch.setattr(inference, "_backend_for", (settings.inference_backend, settings.anthropic_model))
    for f in ("deep-grn-atlas.yaml", "deep-with-ids.yaml"):
        assert ingest_text(session, example(f), f).ok
    session.commit()
    asked = fake.calls
    assert asked > 0
    rebuild(session, backup=False)
    session.commit()
    assert fake.calls == asked  # every judgement came from the library's memory
