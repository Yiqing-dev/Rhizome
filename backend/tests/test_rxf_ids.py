# SPDX-License-Identifier: Apache-2.0
"""File-local ids, cross-reference checks, the grouped error report, opt-in repair and content-based
inbox detection. Fixtures are synthetic; export-drift.yaml mirrors a real failed export."""

import json

from sqlalchemy import select

from conftest import EXAMPLES, ROOT, example
from rhizome.db.models import Edge, Extraction, RawObject
from rhizome.pipeline.graph import Graph
from rhizome.pipeline.ingest import ingest_text
from rhizome.rxf.loader import group_problems, load_rxf, report_for

FIXTURES = ROOT / "backend" / "tests" / "fixtures"


def _kinds(r):
    return {(p.kind, p.field or p.value) for p in r.problems}


# ---- contract ---------------------------------------------------------------------------

def test_ids_exported_at_and_language_are_accepted():
    r = load_rxf(example("deep-with-ids.yaml"))
    assert r.ok, r.problems
    assert r.doc.id == "rxf-2025-0004" and r.doc.language == "zh-CN"
    assert r.doc.exported_at == "2025-06-01T10:30:00+08:00"  # stays a string (no YAML timestamp)
    assert r.doc.claims[0].id == "c1" and r.doc.user_insights[1].links_to == ["m1", "u1", "d1"]


def test_export_drift_is_rejected_with_every_cause():
    r = load_rxf(example("invalid/export-drift.yaml"))
    assert not r.ok
    k = _kinds(r)
    assert ("unexpected_field", "new") in k                    # topics.new
    assert ("unexpected_field", "to") in k and ("unexpected_field", "barrier") in k  # flattened transfer
    assert ("bad_type", "transfer") in k                        # transfer: cross-species (a string)
    assert ("bad_reference", "c9") in k and ("bad_reference", "m7") in k
    assert ("not_a_node", "x1") in k                            # an issue is not a graph node
    assert not r.repairable  # dangling references cannot be repaired, only re-exported


def test_report_groups_by_cause_with_fix_hints():
    r = load_rxf(example("invalid/export-drift.yaml"))
    rep = report_for("export-drift.yaml", r)
    lines = [ln for ln in rep.splitlines() if ln.startswith("- ")]
    assert len(lines) == 5  # 8 problems, 5 causes
    assert any(ln.startswith("- topics.*:") and "(2 places)" in ln for ln in lines)
    assert sum("transfer" in ln for ln in lines) == 1  # two unknown fields + wrong type = one cause
    assert "{type: cross-species" in rep and "{{" not in rep
    assert "Ids declared in this file: t1, t2, c1" in rep


def test_many_items_with_the_same_problem_are_one_line():
    claims = "\n".join(f"  - {{text: c{i}, evidence_type: causal, evidence: Fig. {i}, score: {i}}}" for i in range(10))
    r = load_rxf(f"rxf_version: 1\npaper: {{title: t}}\nclaims:\n{claims}\n")
    groups = group_problems(r.problems)
    assert len(r.problems) == 10 and len(groups) == 1
    assert groups[0]["pattern"] == "claims.*" and groups[0]["field"] == "score"
    assert "- claims.*: field score is not allowed (10 places)" in report_for("x.yaml", r)


def test_duplicate_ids_rejected():
    r = load_rxf("rxf_version: 1\npaper: {title: t}\nclaims:\n"
                 "  - {id: c1, text: a, evidence_type: causal, evidence: F1}\n"
                 "  - {id: c1, text: b, evidence_type: causal, evidence: F2}\n")
    assert ("duplicate_id", "c1") in _kinds(r)


def test_files_without_ids_keep_name_references():
    # older exports: links_to / about are names, resolved against the library on import
    assert load_rxf(example("deep-grn-atlas.yaml")).ok


def test_repair_is_opt_in_and_does_not_touch_the_input():
    text = (FIXTURES / "drift-repairable.yaml").read_text("utf-8")
    plain = load_rxf(text)
    assert not plain.ok and plain.repairable == ["transfer_flattened", "topic_new_flag"]
    assert "rhz ingest --repair" in report_for("drift-repairable.yaml", plain)
    fixed = load_rxf(text, repair=True)
    assert fixed.ok and fixed.repairs == ["transfer_flattened", "topic_new_flag"]
    t = fixed.doc.assets.ideas[0].transfer
    assert (t.type, t.to) == ("cross-species", "t1")
    assert "new" in plain.data["topics"][0]  # the parsed original is not mutated


def test_published_schema_has_the_new_fields():
    spec = json.loads((ROOT / "rxf-spec" / "schema" / "rxf-v1.schema.json").read_text("utf-8"))
    assert {"id", "exported_at", "language"} <= set(spec["properties"])
    assert "id" in spec["$defs"]["Claim"]["properties"]


# ---- ids become edges ----------------------------------------------------------------------

def _out(s, g, ent):
    return {(e.type, g.by_id(e.dst).key) for e in s.execute(select(Edge).where(Edge.src == ent.id)).scalars()}


def test_links_to_ids_become_edges(session):
    r = ingest_text(session, example("deep-with-ids.yaml"), "with-ids.yaml")
    assert r.ok, r.report
    session.flush()
    g = Graph(session)
    u1 = g.by_alias("idea", "交界处的误差可能才是之前空间域划分边界模糊的主因。")
    u2 = g.by_alias("idea", "LeafSeg 的先验思路可以和 u1 的判断一起检验：先校正分割，再看边界是否变清晰。")
    claim = g.by_alias("claim", "分割误差主要集中在表皮与叶肉的交界处。")
    method = g.by_alias("method", "LeafSeg")
    dataset = g.by_key("dataset:CNP9990004")
    topic = g.by_key("topic:spatial domain detection")
    assert {("relates_to", claim.key), ("applicable_to", topic.key)} <= _out(session, g, u1)
    assert {("relates_to", method.key), ("relates_to", u1.key), ("relates_to", dataset.key)} <= _out(session, g, u2)
    assert not u1.attrs.get("links_unresolved") and claim.key in u1.attrs["links"]
    # transfer.to may be a topic id too
    idea = g.by_alias("idea", "用核染色先验约束其他基于 bin 的空间平台的细胞分配。")
    assert ("applicable_to", topic.key) in _out(session, g, idea)
    # review_cards.about -> the entity with that id
    from rhizome.db.models import ReviewCard

    keys = {c.entity_key for c in session.execute(select(ReviewCard)).scalars()}
    assert method.key in keys and claim.key in keys
    # exported_at / language land in L1
    ex = session.execute(select(Extraction).where(Extraction.kind == "rxf")).scalar_one()
    assert ex.output["exported_at"] == "2025-06-01T10:30:00+08:00" and ex.output["language"] == "zh-CN"


def test_id_edges_survive_rebuild(session):
    from rhizome.pipeline.rebuild import rebuild

    assert ingest_text(session, example("deep-with-ids.yaml"), "with-ids.yaml").ok
    session.commit()
    before = session.query(Edge).filter(Edge.type == "relates_to").count()
    rebuild(session, backup=False)
    session.flush()
    assert before == 4 and session.query(Edge).filter(Edge.type == "relates_to").count() == before


def test_repair_keeps_original_in_l0_and_records_in_l1(session):
    text = (FIXTURES / "drift-repairable.yaml").read_text("utf-8")
    assert not ingest_text(session, text, "drift.yaml").ok  # never repaired silently
    r = ingest_text(session, text, "drift.yaml", repair=True)
    assert r.ok and r.repairs == ["transfer_flattened", "topic_new_flag"]
    ex = session.execute(select(Extraction).where(Extraction.kind == "rxf")).scalar_one()
    assert ex.meta["repairs"] == ["transfer_flattened", "topic_new_flag"]
    assert ex.output["assets"]["ideas"][0]["transfer"]["type"] == "cross-species"
    from rhizome import rawstore

    assert rawstore.read(ex.input_hashes[0], "rxf").decode("utf-8") == text  # original bytes, unchanged
    assert session.get(RawObject, ex.input_hashes[0]) is not None


# ---- inbox ---------------------------------------------------------------------------------

def test_inbox_recognises_rxf_by_content_and_handles_spaces(settings, session):
    from rhizome import inbox

    box = settings.inbox
    odd = box / "rxf_version 1.yaml"             # the name a chat client derived from the first line
    odd.write_text(example("light-spatial-domains.yaml"), "utf-8")
    txt = box / "导出 结果.txt"                   # RXF saved as text, spaces + CJK in the name
    txt.write_text("```yaml\n" + example("deep-with-ids.yaml") + "\n```\n", "utf-8")
    notes = box / "notes.txt"
    notes.write_text("just some notes", "utf-8")
    partial = box / "x.yaml.crdownload"
    partial.write_text(example("light-scenic-benchmark.yaml"), "utf-8")

    assert inbox.scan() == 2
    assert (box / "done" / "rxf_version 1.yaml").exists() and (box / "done" / "导出 结果.txt").exists()
    assert notes.exists() and partial.exists()  # not RXF / still downloading: left alone


def test_failed_inbox_file_can_be_repaired_from_the_app(settings):
    from fastapi.testclient import TestClient

    from rhizome import inbox
    from rhizome.api.app import create_app

    box = settings.inbox
    (box / "漂移 文件.yaml").write_text((FIXTURES / "drift-repairable.yaml").read_text("utf-8"), "utf-8")
    inbox.scan()
    assert (box / "error" / "漂移 文件.yaml").exists()

    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    files = c.get("/inbox/failed").json()["files"]
    assert files[0]["name"] == "漂移 文件.yaml" and files[0]["repairable"] == ["transfer_flattened", "topic_new_flag"]
    assert "rhz ingest --repair" in files[0]["report"]
    assert c.post("/inbox/failed/漂移 文件.yaml", json={"repair": False}).status_code == 422
    assert (box / "error" / "漂移 文件.yaml.error.txt").exists()  # failed again: stays, report refreshed
    r = c.post("/inbox/failed/漂移 文件.yaml", json={"repair": True})
    assert r.status_code == 200, r.text
    assert r.json()["repairs"] == ["transfer_flattened", "topic_new_flag"]
    assert (box / "done" / "漂移 文件.yaml").exists()
    assert not (box / "error" / "漂移 文件.yaml.error.txt").exists()
    assert c.get("/inbox/failed").json()["files"] == []
    assert c.post("/inbox/failed/..%2Fsettings.json", json={}).status_code == 404


def test_examples_dir_has_positive_and_negative_id_fixtures():
    assert (EXAMPLES / "deep-with-ids.yaml").exists() and (EXAMPLES / "invalid" / "export-drift.yaml").exists()
