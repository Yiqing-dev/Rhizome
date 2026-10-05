# SPDX-License-Identifier: Apache-2.0
from sqlalchemy import select

from conftest import example
from rhizome.db.models import Edge, Entity, Extraction, RawObject, ReviewCard, Work
from rhizome.pipeline.graph import Graph
from rhizome.pipeline.ingest import ingest_text


def _edge_types(s, src_key):
    g = Graph(s)
    e = g.by_key(src_key)
    return {(ed.type, g.by_id(ed.dst).key) for ed in s.execute(select(Edge).where(Edge.src == e.id)).scalars()}


def test_assets_become_nodes_with_typed_edges(library, session):
    edges = _edge_types(session, "work:doi:10.5555/rhz.example.0001")
    assert ("produces", "dataset:GSE999001") in edges
    assert ("proposes", "method:repo:github.com/rhizome-examples/rootnet") in edges
    assert ("of_organism", "organism:taxon:3702") in edges
    assert ("about", "topic:grn inference") in edges
    assert ("applicable_to", "topic:spatial domain detection") in edges
    g = Graph(session)
    assert g.by_key("dataset:GSE999001").external_id == "GSE999001"


def test_method_extends_and_modality(library, session):
    g = Graph(session)
    rootnet = g.by_key("method:repo:github.com/rhizome-examples/rootnet")
    scenic = g.by_alias("method", "SCENIC")
    assert g.edge(rootnet, scenic, "extends") is not None
    # SCENIC with a repo in one paper and without in another resolves to the same node
    assert scenic.external_id == "github.com/aertslab/scenic"
    assert g.by_alias("method", "github.com/aertslab/scenic").id == scenic.id
    domaingat = g.by_alias("method", "DomainGAT")
    assert g.edge(domaingat, g.by_key("modality:stereo seq"), "of_modality") is not None


def test_user_insight_is_user_idea_linked_to_topic(library, session):
    g = Graph(session)
    idea = g.by_alias("idea", "The accessibility-prior trick could transfer to Stereo-seq data where per-spot "
                              "expression is sparse.")
    assert idea.attrs["origin"] == "user" and idea.attrs["weight"] == 2.0
    assert g.edge(idea, g.by_key("topic:spatial domain detection"), "applicable_to") is not None
    assert "method:repo:github.com/rhizome-examples/rootnet" in idea.attrs["links"]


def test_claim_edges_carry_strength(library, session):
    g = Graph(session)
    work = g.by_key("work:doi:10.5555/rhz.example.0001")
    claims = session.execute(select(Edge).where(Edge.src == work.id, Edge.type == "supports")).scalars().all()
    strengths = sorted(c.attrs["strength"] for c in claims)
    assert strengths == ["strong", "weak"]  # causal + logic_jump correlational


def test_contradiction_goes_to_review_not_graph(library, session):
    from rhizome.db.models import ReviewItem

    items = session.execute(select(ReviewItem).where(ReviewItem.kind == "contradiction")).scalars().all()
    assert len(items) == 1
    assert session.execute(select(Edge).where(Edge.type == "contradicts")).first() is None


def test_deep_export_creates_cards_light_does_not(library, session):
    cards = session.execute(select(ReviewCard)).scalars().all()
    keys = {c.entity_key for c in cards}
    assert "method:repo:github.com/rhizome-examples/rootnet" in keys
    assert "dataset:GSE999001" in keys
    assert not any(k == "dataset:GSE999002" for k in keys)  # from a light export
    user_cards = [c for c in cards if c.priority >= 10]
    assert user_cards, "user insights are prioritised"


def test_duplicate_ingest_is_idempotent(library, session):
    before = session.query(Extraction).count()
    r = ingest_text(session, example("deep-grn-atlas.yaml"), "again.yaml")
    assert r.ok and r.duplicate
    assert session.query(Extraction).count() == before


def test_upgrade_light_to_deep_appends(library, session):
    text = example("light-spatial-domains.yaml").replace("depth: light", "depth: deep") + \
        "review_cards:\n  - q: What does DomainGAT output\n    a: spatial domain labels\n    about: DomainGAT\n"
    r = ingest_text(session, text, "deep-version.yaml")
    assert r.ok and not r.duplicate
    g = Graph(session)
    work = g.by_key("work:doi:10.5555/rhz.example.0003")
    assert work.attrs["depth"] == "deep"
    assert session.query(Extraction).filter(Extraction.work_key == work.key).count() == 2
    assert session.query(Work).count() == 3


def test_preprint_and_published_version_merge(library, session):
    text = example("light-spatial-domains.yaml").replace("10.5555/rhz.example.0003", "10.5555/rhz.journal.0003")
    text = text.replace("year: 2025", "year: 2026")
    r = ingest_text(session, text, "journal.yaml")
    assert r.ok
    assert session.query(Work).count() == 3
    w = session.get(Work, r.work_id)
    assert set(w.dois) == {"10.5555/rhz.example.0003", "10.5555/rhz.journal.0003"}


def test_hallucinated_ids_are_not_stored(settings, session, monkeypatch):
    import rhizome.pipeline.ingest as ing

    monkeypatch.setattr(ing, "check_accession", lambda acc, db: "not_found")
    text = example("deep-grn-atlas.yaml").replace("GSE999001", "GSE999001X")
    r = ingest_text(session, text, "x.yaml")
    assert r.ok
    assert "GSE999001X" in r.suspect
    assert Graph(session).by_key("dataset:GSE999001X") is None
    assert "GSE999001X" in Graph(session).by_key(r.work_key).attrs["suspect_ids"]


def test_raw_layer_is_content_addressed(library, session, settings):
    objs = session.execute(select(RawObject)).scalars().all()
    assert len(objs) == 3
    for o in objs:
        assert (settings.raw_dir / o.sha256[:2] / f"{o.sha256}.yaml").exists()


def test_inbox_moves_files_after_commit(settings, session, monkeypatch):
    from rhizome.pipeline.ingest import ingest_file

    inbox = settings.inbox
    good = inbox / "good.yaml"
    good.write_text(example("light-spatial-domains.yaml"), "utf-8")
    (inbox / "good.pdf").write_bytes(b"%PDF-1.4 synthetic")
    bad = inbox / "bad.yaml"
    bad.write_text(example("invalid/missing-evidence.yaml"), "utf-8")
    crash = inbox / "crash.yaml"
    crash.write_text(example("light-scenic-benchmark.yaml"), "utf-8")

    assert ingest_file(good).ok
    assert not ingest_file(bad).ok
    import rhizome.pipeline.ingest as ing

    real = ing.ingest_text

    def boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ing, "ingest_text", boom)
    assert not ingest_file(crash).ok  # an exception never escapes, so the watcher thread survives
    monkeypatch.setattr(ing, "ingest_text", real)

    assert (inbox / "done" / "good.yaml").exists() and (inbox / "done" / "good.pdf").exists()
    assert (inbox / "error" / "bad.yaml").exists()
    assert "claims.0" in (inbox / "error" / "bad.yaml.error.txt").read_text("utf-8")
    assert (inbox / "error" / "crash.yaml").exists()
    assert "database is locked" in (inbox / "error" / "crash.yaml.error.txt").read_text("utf-8")
    session.expire_all()
    assert session.query(Work).count() == 1  # the crashed file stored nothing
    assert session.query(RawObject).filter(RawObject.kind == "pdf").count() == 1


def test_work_summary_is_indexed(library, session):
    """TL;DR text is attached after the node is created; the index must see the final text."""
    from rhizome.services.search import Filters, search

    hits = search(session, "TCP factors gate meristem elongation zone", Filters(types=("work",)), rerank=False)
    assert hits and hits[0]["key"] == "work:doi:10.5555/rhz.example.0001"
    row = session.execute(select(Entity).where(Entity.key == hits[0]["key"])).scalar_one()
    from sqlalchemy import text as sqltext

    fts = session.execute(sqltext("select text from entity_fts where rowid = :i"), {"i": row.id}).scalar_one()
    assert "TCP factors" in fts


def test_exports_follow_the_resolved_work(library, session):
    """A journal version collapses into the preprint node; its export must show on that card."""
    from rhizome.services.views import entity_card

    text = example("light-spatial-domains.yaml").replace("10.5555/rhz.example.0003", "10.5555/rhz.journal.0003")
    r = ingest_text(session, text.replace("depth: light", "depth: deep"), "journal.yaml")
    assert r.ok and r.work_key == "work:doi:10.5555/rhz.example.0003"
    card = entity_card(session, r.work_id)
    assert len(card["exports"]) == 2
    assert all(ex.work_key == r.work_key for ex in session.query(Extraction).filter(Extraction.kind == "rxf",
               Extraction.output["paper"]["doi"].as_string() == "10.5555/rhz.journal.0003"))
    dup = ingest_text(session, text.replace("depth: light", "depth: deep"), "journal-again.yaml")
    assert dup.duplicate and dup.work_id == r.work_id


def test_pdf_attaches_through_clients(settings, tmp_path):
    from rhizome.client import LocalClient

    c = LocalClient(settings)
    r = c.ingest(example("light-spatial-domains.yaml"), "p.yaml", pdf=b"%PDF-1.4 synthetic")
    assert r["ok"]
    card = c.get(r["work_id"])
    assert card["exports"][0]["has_pdf"] and card["work"]["tier"] == 1


def test_chinese_topic_and_alias(library, session):
    g = Graph(session)
    t = g.by_alias("topic", "空间转录组")
    assert t is not None and t.type == "topic"
    assert session.execute(select(Entity).where(Entity.type == "topic", Entity.status == "candidate")).first()


def test_deep_export_with_pdf_is_tier_2_and_survives_rebuild(settings, session):
    from rhizome.pipeline.rebuild import rebuild
    from rhizome.services.search import Filters, search

    r = ingest_text(session, example("deep-grn-atlas.yaml"), "a.yaml", pdf=b"%PDF-1.4 synthetic")
    assert session.get(Work, r.work_id).tier == 2
    session.commit()
    rebuild(session, backup=False)
    session.flush()
    w = session.query(Work).one()
    assert w.tier == 2
    assert search(session, "single-nucleus multiome atlas", Filters(types=("work",), tier=2))
