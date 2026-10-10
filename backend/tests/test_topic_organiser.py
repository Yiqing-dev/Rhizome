# SPDX-License-Identifier: Apache-2.0
"""'Organise this topic': the agent's proposals are checked against the library, queued as
suggestions, and each one applied is an ordinary decision; nothing changes before that."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("anthropic")

from rhizome.db.models import Edge, Entity, EntityAlias, ReviewItem  # noqa: E402
from rhizome.inference.organise import last_run, organise_topic  # noqa: E402
from rhizome.pipeline.graph import Graph  # noqa: E402
from rhizome.services.review import apply_agent, resolve  # noqa: E402


class Scripted:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        stop, content = self.responses.pop(0)
        return SimpleNamespace(stop_reason=stop, content=content)


def client(*responses):
    m = Scripted(*responses)
    return SimpleNamespace(beta=SimpleNamespace(messages=m)), m


def _answer(topic, other, tagged, untagged_key):
    return {
        "summary": "A small, coherent topic.",
        "definition": "Inferring which regulators control which genes from expression and accessibility data.",
        "definition_confidence": 0.85,
        "aliases": [{"alias": "基因调控网络推断", "confidence": 0.9, "reason": "Chinese name"},
                    {"alias": topic.canonical_name.upper(), "confidence": 0.9, "reason": "same as the name"}],
        "relations": [{"other": other.key, "relation": "merge", "confidence": 0.92, "reason": "acronym and expansion"},
                      {"other": "topic:no such topic", "relation": "parent", "confidence": 0.9, "reason": "x"},
                      {"other": topic.key, "relation": "child", "confidence": 0.9, "reason": "itself"}],
        "misfiled": [{"key": tagged.key, "confidence": 0.7, "reason": "about root development, not GRNs"},
                     {"key": untagged_key, "confidence": 0.9, "reason": "not tagged at all"}],
    }


def _setup(session):
    g = Graph(session)
    topic = g.by_alias("topic", "GRN inference")
    other = g.by_alias("topic", "gene regulatory network inference")
    tagged = session.execute(select(Entity).join(Edge, Edge.src == Entity.id).where(
        Edge.dst == topic.id, Edge.type != "is_a", Entity.type != "work")).scalars().first()
    untagged = session.execute(select(Entity).where(Entity.type == "dataset", Entity.id.not_in(
        select(Edge.src).where(Edge.dst == topic.id)))).scalars().first()
    assert topic and other and tagged and untagged
    return g, topic, other, tagged, untagged


def _tool_use(id_, name, **inp):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)


def test_proposals_are_checked_queued_and_applied(settings, library, session):
    g, topic, other, tagged, untagged = _setup(session)
    assert not (topic.attrs or {}).get("definition")
    c, m = client(
        ("tool_use", [_tool_use("t1", "topic_info", key=topic.key), _tool_use("t2", "tagged_items", key=topic.key, offset=0)]),
        ("end_turn", [SimpleNamespace(type="text", text=json.dumps(_answer(topic, other, tagged, untagged.key)))]),
    )
    out = organise_topic(session, topic.key, client=c, model="claude-opus-5-5")
    assert out["queued"] == 4 and out["not_queued"] == 4  # alias = name, unknown topic, itself, untagged item
    assert out["by_what"] == {"definition": 1, "alias": 1, "merge": 1, "misfiled": 1}
    assert last_run(session, topic.key)["summary"] == "A small, coherent topic."
    tagged_rows = json.loads(m.calls[1]["messages"][2]["content"][1]["content"])["items"]
    assert any(r["key"] == tagged.key for r in tagged_rows)
    assert m.calls[0]["output_config"]["format"]["schema"]["required"][0] == "summary"
    items = {it.payload["what"]: it for it in session.execute(
        select(ReviewItem).where(ReviewItem.kind == "suggestion", ReviewItem.status == "pending")).scalars()}
    assert items["merge"].payload["args"]["into"] in (topic.key, other.key)
    assert items["misfiled"].payload["args"] == {"src": tagged.key, "dst": topic.key,
                                                 "type": items["misfiled"].payload["edge"], "status": "rejected"}
    # nothing has changed yet
    assert not session.execute(select(EntityAlias).where(EntityAlias.alias == "基因调控网络推断")).first()

    # one by one
    assert resolve(session, items["alias"].id, "apply")["status"] == "resolved"
    assert session.execute(select(EntityAlias).where(EntityAlias.alias == "基因调控网络推断")).first()
    assert resolve(session, items["definition"].id, "apply")["decision_id"]
    assert "regulators" in Graph(session).by_key(topic.key).attrs["definition"]
    assert resolve(session, items["misfiled"].id, "skip")["status"] == "skipped"
    # the rest at once, above a confidence bar
    assert apply_agent(session, 0.9)["by_action"] == {"apply": 1}  # the merge (0.92)
    g = Graph(session)
    assert g.resolve_key(topic.key) == g.resolve_key(other.key)
    edge = session.execute(select(Edge).where(Edge.src == tagged.id, Edge.dst == g.by_key(g.resolve_key(topic.key)).id)).scalars().first()
    assert edge is None or edge.status != "rejected"  # the skipped suggestion changed nothing

    # running again proposes nothing it proposed before, skipped ones included
    c2, _ = client(("end_turn", [SimpleNamespace(type="text", text=json.dumps(_answer(topic, other, tagged, untagged.key)))]))
    again = organise_topic(session, g.resolve_key(topic.key), client=c2, model="claude-opus-5-5")
    assert again["by_what"].get("misfiled", 0) == 0 and again["by_what"].get("alias", 0) == 0


def test_agent_failures_change_nothing(settings, library, session):
    _, topic, *_ = _setup(session)
    c, _ = client(("refusal", []))
    assert organise_topic(session, topic.key, client=c)["error"] == "refusal"
    assert organise_topic(session, topic.key)["skipped"]  # no Anthropic backend selected
    with pytest.raises(LookupError):
        organise_topic(session, "topic:does not exist", client=c)
    assert not session.execute(select(ReviewItem).where(ReviewItem.kind == "suggestion")).first()


def test_topic_page_and_job(settings, library, session):
    from rhizome.api.app import create_app

    _, topic, *_ = _setup(session)
    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    page = c.get(f"/topic/{topic.id}/assets").json()
    assert page["ai"] == {"enabled": False, "last_run": None}
    c.patch("/settings", json={"inference_backend": "anthropic"})
    try:
        assert c.get(f"/topic/{topic.id}/assets").json()["ai"]["enabled"] is True
        j = c.post(f"/topic/{topic.id}/organise").json()
        assert j["kind"] == "organise_topic" and j["payload"] == {"topic": topic.key}
        assert c.post("/topic/999999/organise").status_code == 404
        assert c.get("/review", params={"kind": "suggestion"}).status_code == 200
    finally:
        c.patch("/settings", json={"inference_backend": "queue"})


def test_cli_organise_without_backend(settings, library, session):
    from typer.testing import CliRunner

    from rhizome.cli import app

    session.commit()
    r = CliRunner().invoke(app, ["--data-dir", str(settings.data_dir), "organise", "GRN inference"])
    assert r.exit_code == 1 and "anthropic" in r.output
