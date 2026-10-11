# SPDX-License-Identifier: Apache-2.0
"""'Organise the tree': proposals over the whole topic tree are checked, queued as suggestions and
applied as ordinary decisions; a new parent topic is one suggestion holding several decisions."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytest.importorskip("anthropic")

from rhizome.db.models import Edge, ReviewItem  # noqa: E402
from rhizome.inference import organise  # noqa: E402
from rhizome.inference.agent import LibraryTools  # noqa: E402
from rhizome.inference.organise import last_tree_run, organise_tree  # noqa: E402
from rhizome.pipeline.graph import Graph  # noqa: E402
from rhizome.services.review import resolve  # noqa: E402


def _client(*responses):
    calls = []

    def create(**kw):
        calls.append(kw)
        stop, content = responses[len(calls) - 1]
        return SimpleNamespace(stop_reason=stop, content=content)

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))), calls


def _text(obj):
    return [SimpleNamespace(type="text", text=json.dumps(obj))]


def _topics(session):
    g = Graph(session)
    return g, {n: g.by_alias("topic", n) for n in ("GRN inference", "gene regulatory network inference",
                                                     "plant root development", "spatial domain detection")}


def test_list_topics_pages_the_tree(library, session):
    out = json.loads(LibraryTools(session).list_topics(0))
    names = [t["name"] for t in out["topics"]]
    assert "GRN inference" in names and out["more"] is False
    assert all({"key", "status", "tagged", "parents"} <= set(t) for t in out["topics"])
    assert out["topics"][0]["tagged"] >= out["topics"][-1]["tagged"]


def test_tree_proposals_are_checked_and_applied(settings, library, session):
    g, t = _topics(session)
    grn, grn_long, root, spatial = (t[k] for k in ("GRN inference", "gene regulatory network inference",
                                                   "plant root development", "spatial domain detection"))
    answer = {
        "summary": "Small tree with one duplicate.",
        "merges": [{"a": grn.key, "b": grn_long.key, "confidence": 0.95, "reason": "acronym"},
                   {"a": "topic:ghost", "b": grn.key, "confidence": 0.9, "reason": "x"}],
        "hierarchy": [{"child": root.key, "parent": "topic:ghost parent", "confidence": 0.9, "reason": "x"}],
        "groups": [{"name": "regulatory and spatial genomics", "definition": "Genome-scale regulation and tissue context.",
                    "children": [grn.key, spatial.key], "confidence": 0.7, "reason": "both under genomics"},
                   {"name": "lonely", "definition": "", "children": [grn.key], "confidence": 0.9, "reason": "one child"}],
        "promote": [{"key": root.key, "confidence": 0.8, "reason": "recurring"}],
        "hide": [{"key": spatial.key, "confidence": 0.4, "reason": "maybe vague"}],
    }
    c, calls = _client(("tool_use", [SimpleNamespace(type="tool_use", id="t1", name="list_topics", input={"offset": 0})]),
                       ("end_turn", _text(answer)))
    root_was_candidate = root.status == "candidate"
    out = organise_tree(session, client=c, model="claude-opus-5-5")
    expected = {"merge": 1, "group": 1, "reject": 1, **({"confirm": 1} if root_was_candidate else {})}
    assert out["by_what"] == expected
    assert out["not_queued"] >= 3  # ghost merge, ghost parent, one-child group
    assert last_tree_run(session)["summary"] == "Small tree with one duplicate."
    assert json.loads(calls[1]["messages"][2]["content"][0]["content"])["topics"]
    items = {it.payload["what"]: it for it in session.execute(
        select(ReviewItem).where(ReviewItem.kind == "suggestion", ReviewItem.status == "pending")).scalars()}
    group = items["group"]
    assert [s["op"] for s in group.payload["ops"]] == ["create_topic", "add_edge", "add_edge"]
    assert "GRN inference" in group.payload["children"]

    # applying the group creates the parent and both is_a edges
    assert resolve(session, group.id, "apply")["status"] == "resolved"
    g = Graph(session)
    parent = g.by_alias("topic", "regulatory and spatial genomics")
    assert parent is not None and parent.status == "active"
    kids = {e.src for e in session.execute(select(Edge).where(Edge.dst == parent.id, Edge.type == "is_a")).scalars()}
    assert kids == {grn.id, spatial.id}
    # hiding is an ordinary decision too
    assert resolve(session, items["reject"].id, "apply")["status"] == "resolved"
    assert g.by_key(spatial.key).status == "rejected"


def test_tree_budget_and_failures(settings, library, session, monkeypatch):
    g, t = _topics(session)
    monkeypatch.setattr(organise, "TREE_MAX", 1)
    answer = {"summary": "", "merges": [{"a": t["GRN inference"].key, "b": t["gene regulatory network inference"].key,
                                         "confidence": 0.9, "reason": "x"}],
              "hierarchy": [{"child": t["plant root development"].key, "parent": t["GRN inference"].key,
                             "confidence": 0.9, "reason": "y"}], "groups": [], "promote": [], "hide": []}
    c, _ = _client(("end_turn", _text(answer)))
    assert organise_tree(session, client=c)["queued"] == 1  # the budget caps a run
    c, _ = _client(("max_tokens", []))
    assert organise_tree(session, client=c)["error"] == "max_tokens"
    assert organise_tree(session)["skipped"]  # no Anthropic backend selected


def test_map_api(settings, library, session):
    from rhizome.api.app import create_app

    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    assert c.get("/topic-map").json()["ai"] == {"enabled": False, "last_run": None}
    c.patch("/settings", json={"inference_backend": "anthropic"})
    try:
        assert c.get("/topic-map").json()["ai"]["enabled"] is True
        j = c.post("/topic-map/organise").json()
        assert j["kind"] == "organise_tree"
        assert c.post("/topic-map/organise").json()["id"] == j["id"]  # coalesced while queued
    finally:
        c.patch("/settings", json={"inference_backend": "queue"})
