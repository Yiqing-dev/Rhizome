# SPDX-License-Identifier: Apache-2.0
import pytest
from rhizome.pipeline.graph import Graph
from rhizome.services.recall import context_from_code, recall, related_to_work
from rhizome.services.search import Filters, search
from rhizome.services.views import entity_card, neighbors, topic_assets, topic_map


def names(hits):
    return [h["name"] for h in hits]


def test_asset_level_hits_carry_sources(library, session):
    hits = search(session, "RootNet", Filters(types=("method",)))
    assert hits[0]["name"] == "RootNet"
    src = hits[0]["sources"][0]
    assert src["edge"] == "proposes" and "Arabidopsis" in src["title"]


def test_accession_and_chinese_keyword(library, session):
    assert search(session, "GSE999002")[0]["key"] == "dataset:GSE999002"
    assert "空间转录组" in names(search(session, "空间转录组", Filters(types=("topic",))))
    # two-character CJK query falls back to alias substring matching
    assert "空间转录组" in names(search(session, "空间", Filters(types=("topic",))))


def test_filters(library, session):
    mouse = search(session, "single cell", Filters(types=("dataset",), organism="Mus musculus"))
    assert names(mouse) == ["GSE999002"]
    by_role = search(session, "method", Filters(types=("method",), edge_type="evaluates"))
    assert set(names(by_role)) <= {"SCENIC", "GENIE3"} and by_role
    old = search(session, "GRN", Filters(types=("work",), year_max=2024))
    assert all("Benchmarking" in n for n in names(old))


def test_topic_subtree_filter(library, session):
    from rhizome.pipeline import decisions

    g = Graph(session)
    decisions.record(g, "add_edge", {"src": "topic:spatial domain detection", "dst": "topic:空间转录组", "type": "is_a"})
    parent = g.by_key("topic:空间转录组")
    hits = search(session, "domain", Filters(types=("method",), topic=parent.id))
    assert "DomainGAT" in names(hits)
    page = topic_assets(session, parent.id)
    assert any(m["name"] == "DomainGAT" for m in page["columns"]["method"])
    assert any(c["name"] == "spatial domain detection" for c in page["children"])


def test_topic_page_groups_by_role(library, session):
    t = Graph(session).by_key("topic:grn inference")
    page = topic_assets(session, t.id)
    cols = page["columns"]
    assert {m["name"] for m in cols["method"]} >= {"RootNet", "ArchR"}
    assert cols["dataset"][0]["name"] == "GSE999001"
    assert cols["work"]


def test_cards_and_graph_views(library, session):
    g = Graph(session)
    w = g.by_key("work:doi:10.5555/rhz.example.0001")
    card = entity_card(session, w.id)
    assert card["exports"][0]["tldr"] and card["exports"][0]["raw"].startswith("# SYNTHETIC")
    nb = neighbors(session, w.id, hops=2, limit=300)
    assert len(nb["nodes"]) <= 150 and any(n["type"] == "dataset" for n in nb["nodes"])
    tm = topic_map(session, include_candidates=True)
    assert tm["nodes"] and all("counts" in n for n in tm["nodes"])


def test_recall_ranks_forgotten_and_user_ideas(library, session):
    hits = recall(session, "chromatin accessibility priors for sparse spatial spots", min_relevance=0.0)
    assert hits and hits[0]["origin"] == "user"
    assert len(hits) <= 5


def test_related_on_ingest(library, session):
    w3 = library["light-spatial-domains.yaml"]
    assert w3.related and w3.related[0]["title"].startswith("A single-nucleus")
    rel = related_to_work(session, library["deep-grn-atlas.yaml"].work_id)
    assert {r["work_id"] for r in rel} >= {w3.work_id}


def test_context_from_code():
    src = "import scanpy as sc\nfrom pyscenic.grn import grnboost2\nimport os\n# infer GRN on root nuclei\n" \
          "library(ArchR)\n"
    ctx = context_from_code(src)
    assert "scanpy" in ctx and "pyscenic" in ctx and "ArchR" in ctx and "infer GRN" in ctx and "os" not in ctx.split()


def test_retrieval_regression_set(library, session):
    """Retrieval regression set (synthetic). Claims have hashed keys, so match any claim on that question."""
    from pathlib import Path

    from rhizome.services.bench import run

    spec = (Path(__file__).parent / "fixtures" / "bench-synthetic.yaml").read_text("utf-8")
    res = run(session, spec)
    assert res["passed"] and not res["missed"], res
    from rhizome.services.bench import BenchSpecError

    with pytest.raises(BenchSpecError):
        run(session, spec.replace("topic:空间转录组", "topic~no such topic"))


def test_recall_handles_empty_and_import_free_contexts(library, session):
    from rhizome.services.recall import context_from_code, recall

    assert recall(session, "") == [] and recall(session, "   \n ") == []
    ctx = context_from_code("x <- read.csv(f)\nfit <- lm(y ~ x)\n")  # no imports, no comments
    assert ctx.strip()
    recall(session, ctx)  # must not raise
    for blank in ("", "\n\n"):
        assert context_from_code(blank) == ""


def test_notebooks_and_scheduler_directives(library, session):
    import json

    from rhizome.services.recall import context_from_code

    nb = json.dumps({"cells": [
        {"cell_type": "markdown", "source": ["## spatial domain detection on Stereo-seq"]},
        {"cell_type": "code", "source": ["import scanpy as sc\n", "adata = sc.read_h5ad(p)\n"]}]})
    ctx = context_from_code(nb)
    assert "scanpy" in ctx and "spatial domain detection" in ctx and "cells" not in ctx
    ctx = context_from_code("#!/bin/bash\n#SBATCH --mem=64G\n#SBATCH -p gpu\n# run GRN inference\npython x.py\n")
    assert "SBATCH" not in ctx and "GRN inference" in ctx


def test_keyword_query_is_bounded():
    from rhizome.services.search import MAX_FTS_TERMS, _fts_query

    words = " ".join(f"term{i:04d}" for i in range(800)) + " the and with the term0001"
    q = _fts_query(words * 3)
    assert q.count(" OR ") + 1 == MAX_FTS_TERMS
    assert '"the"' not in q and q.count('"term0001"') <= 1


def _fts_text(session, entity_id):
    from sqlalchemy import text

    return session.execute(text("select text from entity_fts where rowid = :i"), {"i": entity_id}).scalar_one()


def test_aliases_are_searchable_after_decisions_and_rebuild(library, session):
    """D8: an alias added by a decision (or an auto-merge) reaches the full-text index at once and
    after a rebuild; every entity has exactly one FTS row, keyed by its id."""
    from sqlalchemy import func, select, text

    from rhizome.db.models import Entity
    from rhizome.pipeline import decisions
    from rhizome.pipeline.graph import Graph
    from rhizome.pipeline.rebuild import rebuild
    from rhizome.services.search import Filters, keyword_ids

    g = Graph(session)
    m = g.by_alias("method", "RootNet")
    decisions.record(g, "add_alias", {"key": m.key, "alias": "根网络推断器"})
    assert "根网络推断器" in _fts_text(session, m.id)
    assert m.id in keyword_ids(session, "根网络推断器", Filters().types)
    session.commit()
    rebuild(session, backup=False)
    m = Graph(session).by_key(m.key)
    assert "根网络推断器" in _fts_text(session, m.id)
    n_fts = session.execute(text("select count(*) from entity_fts")).scalar_one()
    assert n_fts == session.execute(select(func.count(Entity.id))).scalar_one()


def test_upgrade_from_0001_rebuilds_the_fts_index(library, session, settings):
    from alembic import command
    from sqlalchemy import text

    from rhizome.db.session import _alembic_config, dispose_all, get_engine, init_db

    session.commit()
    session.close()
    engine = get_engine(settings)
    with engine.begin() as conn:
        cfg = _alembic_config(engine)
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0001")  # an install made by 0.1.x
    dispose_all()
    init_db(settings)
    with get_engine(settings).connect() as c:
        rows = dict(c.execute(text("select rowid, text from entity_fts")).all())
        ids = [r[0] for r in c.execute(text("select id from entity"))]
        alias_ok = all(a in rows[eid] for eid, a in c.execute(text("select entity_id, alias from entity_alias")))
    assert sorted(rows) == sorted(ids) and alias_ok
