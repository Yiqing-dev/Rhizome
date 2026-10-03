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

    before = sorted(session.execute(select(Entity.key)).scalars())
    rebuild(session, backup=False)
    after = sorted(session.execute(select(Entity.key)).scalars())
    assert before == after


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
