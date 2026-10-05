# SPDX-License-Identifier: Apache-2.0
"""Later exports fill in what earlier ones lacked; ideas you create are linked and reviewed;
review cards keep one identity (and their history) across merges, rebuilds and language switches."""

from sqlalchemy import func, select

from conftest import example
from rhizome.db.models import Edge, ReviewCard, ReviewItem, ReviewLog
from rhizome.pipeline import decisions
from rhizome.pipeline.graph import Graph
from rhizome.pipeline.ingest import ingest_text
from rhizome.pipeline.rebuild import rebuild


def test_later_export_fills_missing_attributes(settings, session):
    """D13: ArchR first known by name only; a later paper says what it does -> kept."""
    from rhizome.pipeline.canonicalize import resolve_free

    assert not (resolve_free(Graph(session), "method", "ArchR").entity.attrs or {}).get("io")
    assert ingest_text(session, example("deep-grn-atlas.yaml"), "b.yaml").ok
    assert Graph(session).by_alias("method", "ArchR").attrs["io"] == "scATAC fragments -> peak matrix"


def _cards(session, key=None):
    q = select(ReviewCard)
    if key:
        q = q.where(ReviewCard.entity_key == key)
    return session.execute(q).scalars().all()


def test_created_idea_is_linked_and_gets_a_card(library, session):
    g = Graph(session)
    ds, m = g.by_key("dataset:GSE999001"), g.by_alias("method", "DomainGAT")
    d = decisions.record(g, "create_idea", {"text": "Use the root atlas to benchmark DomainGAT on plant tissue.",
                                            "links": [ds.key, m.key]})
    idea = g.by_alias("idea", "Use the root atlas to benchmark DomainGAT on plant tissue.")
    out = {(e.type, e.dst) for e in session.execute(select(Edge).where(Edge.src == idea.id)).scalars()}
    assert ("relates_to", ds.id) in out and ("relates_to", m.id) in out
    cards = _cards(session, idea.key)
    assert cards and cards[0].priority == 10
    assert d.id


def test_card_history_survives_merge_and_rebuild(library, session):
    from rhizome.services.cards import grade

    g = Graph(session)
    a, b = g.by_key("topic:spatial domain detection"), g.by_key("topic:grn inference")
    from rhizome.pipeline.materialize import upsert_card

    upsert_card(g, a.key, "What is spatial domain detection?", "Segmenting tissue into regions", "rxf")
    card = _cards(session, a.key)[0]
    grade(session, card.id, 3)
    decisions.record(g, "merge", {"from": a.key, "into": b.key})
    moved = _cards(session, b.key)
    assert [c.q for c in moved] == ["What is spatial domain detection?"]
    assert session.execute(select(func.count()).select_from(ReviewLog).where(ReviewLog.card_id == moved[0].id)).scalar_one() == 1
    session.commit()
    n = len(_cards(session))
    rebuild(session, backup=False)
    session.flush()
    assert len(_cards(session)) == n  # no duplicate appears on rebuild
    kept = [c for c in _cards(session) if c.q == "What is spatial domain detection?"]
    assert len(kept) == 1 and kept[0].introduced_at is not None


def test_generated_cards_follow_the_language_instead_of_duplicating(library, session, settings):
    from rhizome.config import set_settings

    session.commit()
    before = {c.entity_key: c.id for c in _cards(session) if c.origin == "template"}
    assert before
    set_settings(settings.model_copy(update={"language": "zh_CN"}))
    rebuild(session, backup=False)
    session.flush()
    after = {c.entity_key: (c.id, c.q) for c in _cards(session) if c.origin == "template"}
    assert set(after) == set(before) and all(after[k][0] == before[k] for k in before)
    assert any(any("一" <= ch <= "鿿" for ch in q) for _, q in after.values())  # now Chinese


def test_resolving_an_item_whose_entity_is_gone_is_obsolete_not_an_error(library, session):
    from rhizome.services.review import resolve

    it = ReviewItem(kind="retro_tag", payload={"key": "method:gone", "topic": "topic:grn inference", "type": "method"},
                    dedupe_key="retro:x", status="pending")
    session.add(it)
    session.flush()
    assert resolve(session, it.id, "applicable_to")["status"] == "obsolete"
