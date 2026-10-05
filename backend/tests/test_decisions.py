# SPDX-License-Identifier: Apache-2.0
"""Recompute and human review coexist: decisions survive rebuilds."""

from sqlalchemy import select

from rhizome.db.models import Edge, ReviewCard
from rhizome.pipeline import decisions
from rhizome.pipeline.graph import Graph
from rhizome.pipeline.rebuild import rebuild

W1 = "work:doi:10.5555/rhz.example.0001"


def test_rejected_edge_stays_rejected_after_rebuild(library, session):
    g = Graph(session)
    decisions.record(g, "edge_status", {"src": W1, "dst": "topic:spatial domain detection",
                                        "type": "applicable_to", "status": "rejected"})
    session.commit()
    rebuild(session, backup=False)
    g = Graph(session)
    ed = g.edge(g.by_key(W1), g.by_key("topic:spatial domain detection"), "applicable_to")
    assert ed.status == "rejected"


def test_merge_survives_rebuild_and_redirects_new_ingests(library, session):
    from conftest import example
    from rhizome.pipeline.ingest import ingest_text

    g = Graph(session)
    decisions.record(g, "merge", {"from": "topic:gene regulatory network inference", "into": "topic:grn inference"})
    assert g.by_key("topic:gene regulatory network inference").key == "topic:grn inference"  # redirect
    session.commit()
    rebuild(session, backup=False)
    g = Graph(session)
    t = g.by_key("topic:grn inference")
    assert g.by_alias("topic", "gene regulatory network inference").id == t.id
    works = session.execute(select(Edge).where(Edge.dst == t.id, Edge.type == "about")).scalars().all()
    assert len(works) == 2
    # a new export using the merged-away name lands on the surviving node
    text = example("light-spatial-domains.yaml").replace("10.5555/rhz.example.0003", "10.5555/rhz.example.0009")
    text = text.replace("Graph neural networks", "Graph transformers").replace(
        "spatial domain detection, relation: about", "gene regulatory network inference, relation: about")
    r = ingest_text(session, text, "n.yaml")
    assert r.ok
    assert g.by_alias("topic", "gene regulatory network inference").id == t.id


def test_distinct_suppresses_future_merge_proposals(library, session):
    from rhizome.services.review import list_items, resolve

    items = list_items(session, "topic_relation")["items"]
    assert items
    resolve(session, items[0]["id"], "distinct")
    session.commit()
    rebuild(session, backup=False)
    assert not list_items(session, "topic_relation")["items"]


def test_user_topic_survives_rebuild_and_is_active(library, session):
    g = Graph(session)
    decisions.record(g, "create_topic", {"name": "chromatin accessibility", "definition": "open chromatin",
                                         "parent": "GRN inference"})
    session.commit()
    rebuild(session, backup=False)
    g = Graph(session)
    t = g.by_key("topic:chromatin accessibility")
    assert t.status == "active"
    assert g.edge(t, g.by_key("topic:grn inference"), "is_a").status == "confirmed"


def test_card_schedule_survives_rebuild(library, session):
    from rhizome.services.cards import due_cards, grade

    c = due_cards(session, 1)[0]
    grade(session, c["id"], 3)
    session.commit()
    rebuild(session, backup=False)
    card = session.get(ReviewCard, c["id"])
    assert card.introduced_at is not None and card.state


def test_revoke_then_rebuild_restores(library, session):
    g = Graph(session)
    d = decisions.record(g, "edge_status", {"src": W1, "dst": "topic:grn inference", "type": "about",
                                            "status": "rejected"})
    decisions.revoke(g, d.id)
    session.commit()
    rebuild(session, backup=False)
    g = Graph(session)
    assert g.edge(g.by_key(W1), g.by_key("topic:grn inference"), "about").status == "auto"


def test_edit_user_insight(library, session):
    g = Graph(session)
    idea = next(e for e in g.s.execute(select(g.by_key(W1).__class__)).scalars()
                if e.type == "idea" and (e.attrs or {}).get("origin") == "user")
    decisions.record(g, "edit_text", {"key": idea.key, "text": "Accessibility priors may help sparse Stereo-seq spots."})
    session.commit()
    rebuild(session, backup=False)
    assert Graph(session).by_key(idea.key).canonical_name.startswith("Accessibility priors")


def test_bad_decision_rejected(library, session):
    import pytest

    with pytest.raises(decisions.DecisionError):
        decisions.record(Graph(session), "edge_status", {"src": W1, "dst": "x", "type": "nope", "status": "rejected"})


def test_rebuild_is_deterministic(library, session):
    from rhizome.db.models import Entity

    from rhizome.db.models import ReviewItem

    g = Graph(session)
    decisions.record(g, "create_topic", {"name": "my own topic"})
    t = g.by_key("topic:spatial domain detection")
    decisions.record(g, "merge", {"from": t.key, "into": "topic:grn inference"})
    session.commit()
    before = dict(session.execute(select(Entity.key, Entity.id)).all())
    items = dict(session.execute(select(ReviewItem.dedupe_key, ReviewItem.id)
                                 .where(ReviewItem.status == "pending")).all())
    rebuild(session, backup=False)
    after = dict(session.execute(select(Entity.key, Entity.id)).all())
    assert before == after  # same keys, and every key keeps its id (ids are handles)
    items_after = dict(session.execute(select(ReviewItem.dedupe_key, ReviewItem.id)
                                       .where(ReviewItem.status == "pending")).all())
    assert {k: v for k, v in items_after.items() if k in items} == {k: items[k] for k in items_after if k in items}


def test_vector_index_follows_renames_in_process(library, session):
    """A renamed/edited entity must be found by its new text without waiting for a cache miss."""
    from rhizome.services.search import Filters, search

    from rhizome.pipeline.graph import embed_texts, knn

    g = Graph(session)
    search(session, "warm up the vector index", Filters(types=("topic",)), rerank=False)
    topic = g.by_key("topic:benchmarking")
    decisions.record(g, "rename", {"key": topic.key, "name": "zebrafish regeneration atlases"})
    qvec = embed_texts(session, ["zebrafish regeneration atlases"])[0]
    assert knn(session, qvec, types=["topic"], k=1)[0][0] == topic.id  # vector path alone, no FTS


def test_failed_decisions_are_not_persisted(library, session):
    import pytest
    from sqlalchemy import func

    from rhizome.db.models import HumanDecision

    g = Graph(session)
    before = session.execute(select(func.count()).select_from(HumanDecision)).scalar_one()
    with pytest.raises(decisions.DecisionError):
        decisions.record(g, "merge", {"from": "topic:grn inference", "into": "topic:does-not-exist"})
    with pytest.raises(decisions.DecisionError):  # cross-type
        decisions.record(g, "merge", {"from": W1, "into": "topic:grn inference"})
    with pytest.raises(decisions.DecisionError):
        decisions.record(g, "edge_status", {"src": W1, "dst": "topic:nope", "type": "about", "status": "rejected"})
    assert session.execute(select(func.count()).select_from(HumanDecision)).scalar_one() == before
    assert g.by_key("topic:grn inference") is not None  # redirect map untouched
    assert g.by_key(W1).type == "work"


def test_merge_cycle_and_stale_queue_item(library, session):
    import pytest

    from rhizome.services.review import list_items, resolve

    g = Graph(session)
    a, b = "topic:gene regulatory network inference", "topic:grn inference"
    item = next(i for i in list_items(session, "topic_relation")["items"]
                if {i["payload"]["a"], i["payload"]["b"]} == {a, b})
    decisions.record(g, "merge", {"from": a, "into": b})
    with pytest.raises(decisions.DecisionError):  # b -> a would form a cycle through the redirect
        decisions.record(g, "merge", {"from": b, "into": a})
    out = resolve(session, item["id"], "merge")  # queued before the merge: both sides are one entity now
    assert out["status"] == "obsolete" and out["decision_id"] is None
    assert g.by_key(a).key == b
    session.commit()
    rebuild(session, backup=False)
    assert Graph(session).by_key(a).key == b


def test_add_edge_overrides_earlier_rejection(library, session):
    g = Graph(session)
    decisions.record(g, "edge_status", {"src": W1, "dst": "topic:grn inference", "type": "about", "status": "rejected"})
    decisions.record(g, "add_edge", {"src": W1, "dst": "topic:grn inference", "type": "about"})
    assert g.edge(g.by_key(W1), g.by_key("topic:grn inference"), "about").status == "confirmed"


def test_rebuild_keeps_creation_dates(library, session):
    from datetime import datetime

    from rhizome.db.models import Entity, Extraction

    old = datetime(2020, 1, 2, 3, 4, 5)
    for ex in session.query(Extraction):
        ex.created_at = old
    session.commit()
    rebuild(session, backup=False)
    dates = {e.key: e.created_at for e in session.query(Entity).filter(Entity.type != "organism",
                                                                        Entity.type != "modality")}
    assert dates and all(d == old for d in dates.values()), dates
    assert all(ed.created_at == old for ed in session.query(Edge))


def test_stale_decisions_are_skipped_not_fatal(library, session):
    """A confirmed edge between two topics that are later merged becomes a self-loop on replay;
    a decision whose entity no longer exists (e.g. after a model change) must not abort a rebuild."""
    from rhizome.db.models import HumanDecision
    from rhizome.pipeline import decisions
    from rhizome.pipeline.graph import Graph
    from rhizome.pipeline.rebuild import rebuild

    g = Graph(session)
    a, b = g.by_key("topic:spatial domain detection"), g.by_key("topic:grn inference")
    decisions.record(g, "add_edge", {"src": a.key, "dst": b.key, "type": "is_a"})
    decisions.record(g, "merge", {"from": a.key, "into": b.key})
    session.add(HumanDecision(op="rename", payload={"key": "claim:gone-after-model-change", "name": "x"}))
    session.commit()
    out = rebuild(session, backup=False)
    ops = sorted(x["op"] for x in out["decisions_skipped"])
    assert ops == ["add_edge", "rename"] and out["warnings"]
    assert Graph(session).resolve_key(a.key) == b.key  # the merge itself still applies
