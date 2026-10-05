# SPDX-License-Identifier: Apache-2.0
"""Review queue and cards stay usable: Claude's lookups do not count as you seeing an asset, bad
cards can be suspended, cards about rejected assets drop out, idea cards carry a cue, retro
tagging is capped and dismissable, and your insights can be rewritten where you read them."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import example
from rhizome.db.models import Entity, ReviewCard, ReviewItem
from rhizome.pipeline import decisions
from rhizome.pipeline.graph import Graph


@pytest.fixture()
def api(settings, library, session):
    from rhizome.api.app import create_app

    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    return c


def test_machine_lookups_do_not_reset_forgetting(library, session, api):
    from rhizome.client import LocalClient
    from rhizome.services import recall

    g = Graph(session)
    m = g.by_alias("method", "DomainGAT")
    recall._seen.clear()
    LocalClient().get(m.id, touch=False)
    LocalClient().get_by_key(m.key, touch=False)
    assert api.get(f"/entity/{m.id}", params={"touch": "false"}).status_code == 200
    assert api.get("/entity/by-key", params={"key": m.key, "touch": "false"}).status_code == 200
    assert not any(k == m.key for _, k in recall._seen)
    api.get(f"/entity/{m.id}")  # you opening the card: seen
    assert any(k == m.key for _, k in recall._seen)
    recall._seen.clear()
    import rhizome.mcp_server as mcp

    mcp._client = LocalClient()
    assert "DomainGAT" in mcp.rhz_get(m.key)
    assert not any(k == m.key for _, k in recall._seen)


def test_cards_can_be_suspended_and_follow_rejections(library, session, api, settings):
    from rhizome.services.cards import due_cards, due_count

    session.commit()
    cards = due_cards(session, 50)
    assert cards
    card = cards[0]
    r = api.post(f"/cards/{card['id']}/suspend", json={"suspended": True})
    assert r.status_code == 200 and r.json()["suspended"]
    session.expire_all()
    assert card["id"] not in {c["id"] for c in due_cards(session, 50)}
    # never again for this asset: its other cards go too, and a rebuild makes none
    other = next(c for c in due_cards(session, 50) if c["origin"] == "template")
    r = api.post(f"/cards/{other['id']}/suspend", json={"suspended": True, "entity": True})
    assert r.status_code == 200
    session.expire_all()
    e = session.execute(select(Entity).where(Entity.key == other["entity_key"])).scalar_one()
    assert e.attrs.get("no_cards") and not any(c["entity_key"] == e.key for c in due_cards(session, 50))
    # cards about a rejected asset are not due (decided at query time)
    live = due_cards(session, 50)
    victim = next(c for c in live if c["entity_key"].startswith(("dataset:", "method:")))
    before = due_count(session)
    decisions.record(Graph(session), "reject_entity", {"key": victim["entity_key"]})
    session.flush()
    assert victim["id"] not in {c["id"] for c in due_cards(session, 50)} and due_count(session) < before


def test_idea_cards_have_a_cue_or_none(settings, session):
    from rhizome.pipeline.ingest import ingest_text

    assert ingest_text(session, example("deep-grn-atlas.yaml"), "a.yaml").ok
    assert ingest_text(session, example("deep-with-ids.yaml"), "b.yaml").ok
    g = Graph(session)
    cards = {c.entity_key: c for c in session.execute(select(ReviewCard)).scalars()}
    transfer_idea = g.by_alias("idea", "用核染色先验约束其他基于 bin 的空间平台的细胞分配。")
    assert "spatial domain detection" in cards[transfer_idea.key].q
    user = g.by_alias("idea", "The accessibility-prior trick could transfer to Stereo-seq data where per-spot expression is sparse.")
    assert user.key in cards and ("RootNet" in cards[user.key].q or "spatial domain detection" in cards[user.key].q)
    model_with_cue = g.by_alias("idea", "Use chromatin-accessibility priors to constrain GRN edges when expression data are sparse.")
    assert "GRN inference" in cards[model_with_cue.key].q
    titles = [c.q for c in cards.values() if c.origin == "template"]
    assert len(titles) == len(set(titles)), "every template card asks a different question"


def test_retro_tagging_is_capped_pageable_and_dismissable(library, session, settings, api):
    from rhizome.pipeline.retro import retro_tag
    from rhizome.services.review import dismiss, list_items

    settings.thresholds.retro_queue_max = 2
    settings.thresholds.retro_rerank_min = 0.0
    decisions.record(Graph(session), "create_topic", {"name": "chromatin accessibility",
                                                      "definition": "Open chromatin assays such as ATAC-seq"})
    out = retro_tag(session, "topic:chromatin accessibility", k=50)
    assert out["queued"] == 2 and out["remaining"] >= 1
    first = {i["payload"]["key"] for i in list_items(session, "retro_tag")["items"]}
    scores = [i["score"] for i in list_items(session, "retro_tag")["items"]]
    assert scores == sorted(scores, reverse=True)
    again = retro_tag(session, "topic:chromatin accessibility", k=50)
    assert again["queued"] == 2  # pages on: the next best two, never the same keys
    assert len({i["payload"]["key"] for i in list_items(session, "retro_tag")["items"]} - first) == 2
    # redefining the topic retires the pending questions asked against the old definition
    decisions.record(Graph(session), "create_topic", {"name": "chromatin accessibility",
                                                      "definition": "ATAC-seq, DNase-seq and single-cell variants"})
    assert list_items(session, "retro_tag")["total"] == 0
    retro_tag(session, "topic:chromatin accessibility", k=50)
    assert list_items(session, "retro_tag")["total"] == 2
    assert dismiss(session, "retro_tag", "topic:chromatin accessibility") == 2
    assert list_items(session, "retro_tag")["total"] == 0
    assert session.execute(select(ReviewItem).where(ReviewItem.status == "skipped")).first() is not None
    session.commit()
    r = api.post("/review/dismiss", json={"kind": "merge"})
    assert r.status_code == 200 and "dismissed" in r.json()


def test_insights_are_shown_with_their_entity_and_editable(library, session):
    from typer.testing import CliRunner

    from rhizome.cli import app
    from rhizome.services.views import entity_card

    g = Graph(session)
    w = g.by_key("work:doi:10.5555/rhz.example.0001")
    ins = entity_card(session, w.id)["exports"][0]["user_insights"][0]
    assert ins["key"].startswith("idea:") and ins["name"] == ins["text"] and not ins["edited"]
    session.commit()
    r = CliRunner().invoke(app, ["--data-dir", str(g.s.get_bind().url).split("sqlite:///")[1].rsplit("/", 1)[0],
                                 "edit", ins["key"], "Accessibility priors should help sparse Stereo-seq spots."],
                           catch_exceptions=False)
    assert r.exit_code == 0, r.output
    session.expire_all()
    ins2 = entity_card(session, w.id)["exports"][0]["user_insights"][0]
    assert ins2["name"] == "Accessibility priors should help sparse Stereo-seq spots." and ins2["edited"]
    assert ins2["text"] == ins["text"]  # the export keeps what you wrote then
