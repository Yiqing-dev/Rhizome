# SPDX-License-Identifier: Apache-2.0
"""Correction channels: retract / replace a paper's exports, reject a wrong asset, undo, and
revoked merges giving everything back."""

import json

from sqlalchemy import select
from typer.testing import CliRunner

from conftest import example
from rhizome.db.models import Extraction, ReviewCard, ReviewItem
from rhizome.pipeline import decisions
from rhizome.pipeline.graph import Graph
from rhizome.pipeline.ingest import ingest_text
from rhizome.pipeline.rebuild import rebuild
from rhizome.services.search import Filters, search

W1, W2 = "work:doi:10.5555/rhz.example.0001", "work:doi:10.5555/rhz.example.0002"


def _exports(session, key):
    return session.execute(select(Extraction.id).where(Extraction.work_key == key, Extraction.kind == "rxf",
                                                       Extraction.is_current)).scalars().all()


def test_revoking_a_paper_merge_gives_its_exports_back(library, session):
    g = Graph(session)
    d = decisions.record(g, "merge", {"from": W2, "into": W1})
    session.commit()
    assert len(_exports(session, W1)) == 2 and _exports(session, W2) == []
    decisions.revoke(Graph(session), d.id)
    rebuild(session, backup=False)
    session.flush()
    assert len(_exports(session, W1)) == 1 and len(_exports(session, W2)) == 1  # D26


def test_revoked_merge_comes_back_to_the_review_queue(library, session):
    g = Graph(session)
    a, b = "topic:spatial domain detection", "topic:grn inference"
    session.add(ReviewItem(kind="merge", payload={"a": a, "b": b}, dedupe_key=f"merge:{min(a, b)}|{max(a, b)}",
                           status="resolved"))
    d = decisions.record(g, "merge", {"from": a, "into": b})
    session.commit()
    decisions.revoke(Graph(session), d.id)
    assert session.execute(select(ReviewItem).where(ReviewItem.dedupe_key == f"merge:{min(a, b)}|{max(a, b)}")
                           ).first() is None  # D28: not blocked forever


def test_corrected_reexport_replaces_the_old_one(library, session):
    fixed = example("deep-grn-atlas.yaml").replace("accession: GSE999001", "accession: GSE999009")
    r = ingest_text(session, fixed, "fixed.yaml")
    assert r.existing_exports and not r.replaced  # told, nothing retracted yet
    session.rollback()
    r = ingest_text(session, fixed, "fixed.yaml", replace=True)
    assert r.replaced and r.rebuild_job
    session.commit()
    rebuild(session, backup=False)
    session.flush()
    g = Graph(session)
    assert g.by_key("dataset:GSE999009") is not None
    assert g.by_key("dataset:GSE999001") is None  # only the old export asserted it
    assert len(_exports(session, W1)) == 1


def test_retract_is_undoable(library, session):
    g = Graph(session)
    d = decisions.record(g, "retract", {"work": W2})
    session.commit()
    rebuild(session, backup=False)
    session.flush()
    assert Graph(session).by_key(W2) is None
    decisions.revoke(Graph(session), d.id)
    rebuild(session, backup=False)
    session.flush()
    assert Graph(session).by_key(W2) is not None


def test_rejected_asset_is_hidden_everywhere_and_stays_hidden(library, session):
    from rhizome.services.datasets import dataset_info
    from rhizome.services.recall import recall

    g = Graph(session)
    ds = g.by_key("dataset:GSE999001")
    assert search(session, "GSE999001")[0]["key"] == ds.key
    decisions.record(g, "reject_entity", {"key": ds.key})
    session.commit()
    for _ in range(2):  # immediately, and after a rebuild (the decision is replayed)
        assert ds.key not in [h["key"] for h in search(session, "GSE999001")]
        assert ds.key not in [h["key"] for h in search(session, "", Filters(types=("dataset",)))]
        assert dataset_info(session, "GSE999001") is None
        assert ds.key not in [h["key"] for h in recall(session, "GSE999001 root snRNA-seq atlas")]
        cards = session.execute(select(ReviewCard).where(ReviewCard.entity_key == ds.key)).scalars().all()
        assert all(c.suspended for c in cards)
        rebuild(session, backup=False)
        session.flush()


def test_cli_corrections_and_undo(settings):
    from rhizome.cli import app

    runner = CliRunner()
    rhz = lambda *a: runner.invoke(app, ["--data-dir", str(settings.data_dir), *a], catch_exceptions=False)  # noqa: E731
    from conftest import EXAMPLES

    assert rhz("ingest", str(EXAMPLES / "deep-grn-atlas.yaml")).exit_code == 0
    r = rhz("rename", "method:repo:github.com/rhizome-examples/rootnet", "RootNet v2")
    assert r.exit_code == 0 and "rhz undo" in r.output
    rows = json.loads(rhz("--json", "decisions").output)
    assert rows[0]["op"] == "rename"
    assert rhz("undo", str(rows[0]["id"])).exit_code == 0
    assert json.loads(rhz("--json", "decisions").output)[0]["revoked_at"]
    assert rhz("reject", "dataset:GSE999001").exit_code == 0
    assert rhz("reject", "dataset:does-not-exist").exit_code == 1
