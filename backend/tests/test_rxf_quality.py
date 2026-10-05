# SPDX-License-Identifier: Apache-2.0
"""Exports survive the chat's framing, error reports say what to move or rename, topics carry
aliases and acronyms are noticed, thresholds follow the model, replay checks before it deletes,
keys are pinned, and raw/ alone can rebuild the library."""

import json
import shutil

import pytest
from sqlalchemy import select

from conftest import example
from rhizome.db.models import Entity, Extraction, ReviewItem
from rhizome.pipeline.graph import Graph
from rhizome.pipeline.ingest import ingest_text
from rhizome.rxf.loader import load_rxf, report_for, strip_fences


# ---- X6 -------------------------------------------------------------------------------------

def test_export_is_found_inside_prose_and_fences(tmp_path):
    body = example("light-scenic-benchmark.yaml")
    wrapped = "Here is the export you asked for:\n\n~~~yaml\n" + body + "\n~~~\n\nLet me know if anything is off."
    assert strip_fences(wrapped).strip() == body.strip()
    assert load_rxf(wrapped).ok
    two = "First a note:\n```\nnot: rxf\n```\nThen:\n```yaml\n" + body + "\n```\n"
    assert load_rxf(two).ok
    assert load_rxf("Sure! Below is the document.\n\n" + body).ok  # prose, no fence
    from rhizome.inbox import looks_like_rxf

    f = tmp_path / "chat.txt"
    f.write_text(wrapped, "utf-8")
    assert looks_like_rxf(f)


# ---- X2 -------------------------------------------------------------------------------------

def test_error_report_says_move_or_rename_and_lists_values():
    doc = example("light-scenic-benchmark.yaml")
    moved = doc.replace("assets:\n  datasets:", "datasets:").replace("  methods:", "methods:")
    moved = moved.replace("    - {accession: GSE999002", "  - {accession: GSE999002").replace("    - {name: SCENIC", "  - {name: SCENIC").replace("    - {name: GENIE3", "  - {name: GENIE3")
    r = load_rxf(moved)
    rep = report_for("x.yaml", r)
    assert "under assets:" in rep and "remove datasets" not in rep
    renamed = doc.replace("  - text: Chromatin", "  - claim: Chromatin").replace("  venue:", "  journal:")
    rep = report_for("x.yaml", load_rxf(renamed))
    assert "rename claim to text" in rep
    no_rel = doc.replace("{name: benchmarking, relation: about}", "{name: benchmarking}")
    rep = report_for("x.yaml", load_rxf(no_rel))
    assert "add relation: one of about, applicable_to" in rep
    bad_item = doc.replace("  - text: Chromatin accessibility changes do not precede expression changes along developmental trajectories.\n    evidence_type: correlational\n    evidence: Supplementary Fig. 7\n    boundary: Mouse hematopoiesis only.",
                           "  - Chromatin accessibility changes do not precede expression changes.")
    rep = report_for("x.yaml", load_rxf(bad_item))
    assert "claims has the wrong type; expected object with" in rep and "evidence" in rep
    many = no_rel.replace("  - claim", "  - claim").replace("paper:\n  doi:", "paper:\n  journal: X\n  doi:")
    many = many.replace("  - text: Chromatin", "  - claim: Chromatin").replace("    - {name: GENIE3", "    - {title: GENIE3")
    rep = report_for("x.yaml", load_rxf(many))
    assert "re-read the skeleton" in rep


# ---- M4 -------------------------------------------------------------------------------------

def test_topic_aliases_and_acronyms_meet_existing_topics(settings, session):
    from rhizome.pipeline.canonicalize import acronym_expands, resolve_free

    assert ingest_text(session, example("deep-grn-atlas.yaml"), "a.yaml").ok
    g = Graph(session)
    grn = g.by_key("topic:grn inference")
    # an export that names the topic in full, with the acronym as an alias, lands on the same topic
    doc = example("light-scenic-benchmark.yaml").replace(
        "{name: gene regulatory network inference, relation: about}",
        "{name: gene regulatory network inference, relation: about, aliases: [GRN inference, 基因调控网络推断]}")
    assert ingest_text(session, doc, "b.yaml").ok
    assert g.by_alias("topic", "基因调控网络推断").id == grn.id
    assert g.by_alias("topic", "gene regulatory network inference").id == grn.id
    # without aliases, the initials match turns into a question instead of a second topic
    assert acronym_expands("GRN inference", "gene regulatory network inference")
    assert acronym_expands("GRN", "gene regulatory network") and not acronym_expands("GRN", "general network")
    r = resolve_free(g, "topic", "spatial domain detection method", status="candidate")  # control: unrelated
    assert r.created
    assert ingest_text(session, example("light-spatial-domains.yaml"), "c.yaml").ok
    r = resolve_free(g, "method", "Spatially Resolved Domain Mapper", status="active")
    r2 = resolve_free(g, "method", "SRDM", status="active")
    assert r2.created and r2.entity.id != r.entity.id
    item = session.execute(select(ReviewItem).where(ReviewItem.kind == "merge", ReviewItem.status == "pending",
                                                    ReviewItem.payload["a"].as_string() == r2.entity.key)).scalar_one()
    assert item.payload["b"] == r.entity.key


# ---- M5 -------------------------------------------------------------------------------------

def test_thresholds_follow_the_embedder_unless_set(tmp_path):
    from rhizome.config import THRESHOLD_PROFILES, Settings, Thresholds

    base = Settings(data_dir=tmp_path / "a")
    assert base.thresholds.merge_auto == Thresholds().merge_auto
    bge = Settings(data_dir=tmp_path / "a", embedder="bge-m3")
    assert bge.thresholds.merge_auto == THRESHOLD_PROFILES["bge-m3"]["merge_auto"]
    assert bge.thresholds.topic_promote_works == Thresholds().topic_promote_works  # not in the profile
    mine = Settings(data_dir=tmp_path / "a", embedder="bge-m3", thresholds={"merge_auto": 0.97})
    assert mine.thresholds.merge_auto == 0.97
    assert mine.thresholds.synthesis_sim == THRESHOLD_PROFILES["bge-m3"]["synthesis_sim"]
    back = mine.with_embedder("hashing")
    assert back.thresholds.merge_auto == 0.97 and back.thresholds.synthesis_sim == Thresholds().synthesis_sim
    assert base.with_embedder("bge-m3").thresholds.recall_min == THRESHOLD_PROFILES["bge-m3"]["recall_min"]


# ---- U4 -------------------------------------------------------------------------------------

def test_rebuild_checks_stored_exports_before_deleting(library, session):
    from rhizome.pipeline.rebuild import RebuildAborted, rebuild

    ex = session.execute(select(Extraction).where(Extraction.kind == "rxf").order_by(Extraction.id)).scalars().first()
    ex.output = {**ex.output, "paper": {**ex.output["paper"], "journal": "sneaked in"}}
    session.flush()
    n_entities = session.execute(select(Entity)).scalars().all()
    with pytest.raises(RebuildAborted) as e:
        rebuild(session, backup=False)
    assert e.value.failures[0]["extraction_id"] == ex.id and "journal" in e.value.failures[0]["error"]
    assert len(session.execute(select(Entity)).scalars().all()) == len(n_entities)  # nothing was deleted
    out = rebuild(session, backup=False, force=True)
    assert [f["extraction_id"] for f in out["failed"]] == [ex.id] and out["extractions"] >= 2
    assert any("did not replay" in w for w in out["warnings"])


def test_stored_rows_are_parsed_by_their_version():
    from rhizome.rxf.schema import parse_stored, stored_version

    assert stored_version("rxf-v1") == 1 and stored_version(None) == 1
    data = load_rxf(example("light-scenic-benchmark.yaml")).doc.model_dump(mode="json")
    assert parse_stored(data, "rxf-v1").paper.title.startswith("Benchmarking")
    with pytest.raises(ValueError):
        parse_stored(data, "rxf-v9")


# ---- U5 -------------------------------------------------------------------------------------

def test_norm_and_keys_are_pinned():
    """Changing norm() changes every durable key: see the comment on NORM_VERSION in text.py."""
    from rhizome.pipeline.canonicalize import free_key
    from rhizome.text import NORM_VERSION, norm

    assert NORM_VERSION == 1
    assert norm("  Spatial-Domain Detection (v2) ") == "spatial domain detection v2"
    assert norm("ＧＲＮ　推断") == "grn 推断"
    assert free_key("topic", "GRN Inference") == "topic:grn inference"
    assert free_key("idea", "Use priors.") == "idea:6a35298f047acbff3158"
    assert free_key("method", "x" * 130) == "method:" + free_key("method", "x" * 130).split(":")[1]
    assert len(free_key("method", "x" * 130).split(":")[1]) == 20  # long names hash


# ---- D30 ------------------------------------------------------------------------------------

def test_raw_folder_alone_rebuilds_the_library(settings, session, tmp_path, monkeypatch):
    from datetime import datetime

    from rhizome import rawstore
    from rhizome.config import Settings, set_settings
    from rhizome.db.session import dispose_all, init_db, session_scope
    from rhizome.pipeline.ingest import recover_from_raw

    oa = {"id": "W1", "doi": "10.5555/rhz.example.0002", "title": "Benchmarking gene regulatory network inference on single-cell data",
          "year": 2024, "type": "article", "venue": "J", "authors": ["A"], "abstract": "An abstract from OpenAlex.",
          "referenced_works": [], "oa_url": None, "ids": {}}
    when = datetime(2024, 3, 4, 5, 6, 7)
    r = ingest_text(session, example("light-scenic-benchmark.yaml"), "scenic.yaml", openalex_record=oa, captured_at=when)
    assert r.ok
    r2 = ingest_text(session, example("deep-grn-atlas.yaml"), "atlas.yaml", pdf=b"%PDF-1.4 synthetic", openalex_record=None)
    assert r2.ok
    session.commit()
    metas = rawstore.list_meta()
    assert [m["filename"] for m in metas] == ["scenic.yaml", "atlas.yaml"]
    assert metas[0]["openalex_sha"] and metas[0]["captured_at"].startswith("2024-03-04T05:06:07")
    assert metas[1]["pdf_sha"] and rawstore.read(metas[1]["pdf_sha"], "pdf").startswith(b"%PDF")
    assert json.loads(rawstore.read(metas[0]["openalex_sha"], "openalex"))["abstract"] == "An abstract from OpenAlex."
    keys_before = sorted(session.execute(select(Entity.key).where(Entity.type == "work")).scalars())
    # a new, empty library that only has the raw folder
    fresh = tmp_path / "fresh"
    shutil.copytree(settings.raw_dir, fresh / "raw")
    dispose_all()
    st2 = Settings(data_dir=fresh, offline=True, language="en")
    set_settings(st2)
    st2.ensure_dirs()
    init_db(st2)
    import rhizome.external.openalex as oamod

    monkeypatch.setattr(oamod, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("went online")))
    with session_scope(st2) as s2:
        out = recover_from_raw(s2)
        assert (out["recovered"], out["failed"]) == (2, 0), out
        assert sorted(s2.execute(select(Entity.key).where(Entity.type == "work")).scalars()) == keys_before
        ex = s2.execute(select(Extraction).where(Extraction.kind == "rxf").order_by(Extraction.id)).scalars().first()
        assert ex.created_at == when
        w = Graph(s2).by_key("work:openalex:W1") or Graph(s2).by_key("work:doi:10.5555/rhz.example.0002")
        assert w.attrs.get("abstract") == "An abstract from OpenAlex."
        assert recover_from_raw(s2)["already_present"] == 2  # idempotent
