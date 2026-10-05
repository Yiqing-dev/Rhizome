# SPDX-License-Identifier: Apache-2.0
"""Materialisation keeps provenance straight: your ideas stay yours, a paper's stance is honoured,
several papers' reports about one asset do not overwrite each other, file-local ids stay out of
the library, and human merges survive rebuild ordering."""

from sqlalchemy import select

from conftest import example
from rhizome.db.models import Edge, HumanDecision, ReviewItem
from rhizome.pipeline import decisions
from rhizome.pipeline.canonicalize import resolve_free
from rhizome.pipeline.graph import Graph, entity_text
from rhizome.pipeline.ingest import ingest_text

LIGHT = """rxf_version: 1
prompt_version: light-review-v1
depth: light
paper:
  doi: {doi}
  title: {title}
  year: 2024
  type: [benchmark]
  organisms: [Arabidopsis thaliana]
  modalities: [snRNA-seq]
tldr:
  - {title}.
topics:
  - {{name: gene regulatory network inference, relation: about}}
claims: []
assets:
  datasets:
    - {{accession: GSE999001, database: GEO, organism: Arabidopsis thaliana, tissue: {tissue}, modality: snRNA-seq, role: {role}}}
  methods: []
"""


def _doc(doi: str, title: str, tissue: str, role: str) -> str:
    return LIGHT.format(doi=doi, title=title, tissue=tissue, role=role)


A = "Use chromatin accessibility priors to constrain GRN edges when expression data are sparse"
A_USER = A + " indeed"


def _lenient(settings):
    from rhizome.config import Thresholds, set_settings

    set_settings(settings.model_copy(update={"thresholds": Thresholds(merge_auto=0.7)}))


def test_user_idea_is_never_silently_merged_into_a_model_idea(library, session, settings):
    _lenient(settings)
    g = Graph(session)
    model = resolve_free(g, "idea", A, attrs={"origin": "model"}).entity
    assert resolve_free(g, "idea", A_USER).entity.id == model.id  # close enough to auto-merge (control)
    session.rollback()
    g = Graph(session)
    model = resolve_free(g, "idea", A, attrs={"origin": "model"}).entity
    r = resolve_free(g, "idea", A_USER, attrs={"origin": "user", "weight": 2.0}, origin="user")
    assert r.created and r.entity.id != model.id
    assert (g.by_id(model.id).attrs or {}).get("origin") == "model"  # the model's idea is not relabelled
    item = session.execute(select(ReviewItem).where(ReviewItem.kind == "merge", ReviewItem.status == "pending",
                                                    ReviewItem.payload["a"].as_string() == r.entity.key)).scalar_one()
    assert item.payload["b"] == model.key
    # the user accepts the merge, in either direction: their wording and origin survive
    decisions.record(g, "merge", {"from": r.entity.key, "into": model.key})
    kept = g.by_id(model.id)
    assert kept.canonical_name == A_USER and kept.attrs["origin"] == "user" and kept.attrs["weight"] == 2.0
    assert g.by_alias("idea", A).id == kept.id


def test_contradicting_paper_does_not_support_what_its_claim_entails(library, session, monkeypatch):
    from rhizome.pipeline import materialize as mat
    from rhizome.pipeline.canonicalize import ClaimResolution

    g = Graph(session)
    other = session.execute(select(mat.Entity).where(mat.Entity.type == "claim")).scalars().first()
    real = mat.resolve_claim

    def with_entailment(g, text, attrs):
        r = real(g, text, attrs)
        return ClaimResolution(r.entity, r.created, [other], [(other, 0.9)])

    monkeypatch.setattr(mat, "resolve_claim", with_entailment)
    doc = example("light-scenic-benchmark.yaml").replace("evidence_type: correlational",
                                                         "evidence_type: correlational\n    stance: contradicts")
    doc = doc.replace("10.5555/rhz.example.0002", "10.5555/rhz.example.0099").replace("Benchmarking gene", "Re-benchmarking gene")
    res = ingest_text(session, doc, "contra.yaml")
    assert res.ok, res.report
    work = g.by_key("work:doi:10.5555/rhz.example.0099")
    assert g.edge(work, other, "supports") is None
    item = session.execute(select(ReviewItem).where(ReviewItem.kind == "contradiction",
                                                    ReviewItem.payload["work"].as_string() == work.key)).scalar_one()
    assert item.payload["stance"] == "contradicts" and item.payload["strength"] and item.payload["extraction_id"]
    # accepting: the paper argued against its own claim, which contradicts `other` -> it sides with `other`
    from rhizome.services.review import resolve

    out = resolve(session, item.id, "accept")
    assert out["decision_id"]
    ed = g.edge(work, other, "supports")
    assert ed is not None and ed.status == "confirmed"
    assert ed.evidence == "Supplementary Fig. 7" and ed.extraction_id == item.payload["extraction_id"]
    assert ed.attrs["strength"] == "medium" and ed.attrs["evidence_type"] == "correlational" and ed.attrs["via"] == "nli"


def test_edge_provenance_is_replaced_per_extraction_not_mixed(library, session):
    g = Graph(session)
    work, ds = g.by_key("work:doi:10.5555/rhz.example.0001"), g.by_key("dataset:GSE999001")
    ed = g.upsert_edge(work, ds, "evaluates", extraction_id=101, evidence="Fig 1 (light)", confidence=1.0,
                       attrs={"evidence_type": "correlational"})
    g.upsert_edge(work, ds, "evaluates", extraction_id=101, evidence="ignored", confidence=0.4)  # same export: max
    assert ed.confidence == 1.0 and ed.evidence == "Fig 1 (light)"
    g.upsert_edge(work, ds, "evaluates", extraction_id=102, evidence="Fig 3 (deep)", confidence=0.5,
                  attrs={"logic_jump": True, "strength": "weak"})
    assert (ed.evidence, ed.extraction_id, ed.confidence) == ("Fig 3 (deep)", 102, 0.5)
    assert ed.attrs["logic_jump"] is True and "evidence_type" not in ed.attrs
    assert ed.attrs["evidence_records"] == [{"extraction_id": 101, "evidence": "Fig 1 (light)", "confidence": 1.0,
                                             "evidence_type": "correlational"}]


def test_reported_attributes_are_not_last_writer_wins(settings, session):
    g = Graph(session)
    assert ingest_text(session, _doc("10.5555/x.1", "Producer paper", "root", "produces"), "p.yaml").ok
    assert ingest_text(session, _doc("10.5555/x.2", "User paper", "shoot", "uses"), "u.yaml").ok
    ds = g.by_key("dataset:GSE999001")
    assert ds.attrs["tissue"] == "root"
    assert ds.attrs["reported"]["tissue"]["values"] == ["root", "shoot"]
    session.rollback()
    g = Graph(session)
    assert ingest_text(session, _doc("10.5555/x.2", "User paper", "shoot", "uses"), "u.yaml").ok
    assert ingest_text(session, _doc("10.5555/x.3", "Another user", "leaf", "uses"), "v.yaml").ok
    assert g.by_key("dataset:GSE999001").attrs["tissue"] == "shoot"  # first report fills, a second user does not flip
    assert ingest_text(session, _doc("10.5555/x.1", "Producer paper", "root", "produces"), "p.yaml").ok
    ds = g.by_key("dataset:GSE999001")
    assert ds.attrs["tissue"] == "root" and ds.attrs["reported"]["tissue"]["producer"] == "root"
    assert set(ds.attrs["reported"]["tissue"]["values"]) == {"root", "shoot", "leaf"}
    assert "id" not in ds.attrs


def test_file_local_ids_do_not_leak(settings, session):
    assert ingest_text(session, example("deep-with-ids.yaml"), "ids.yaml").ok
    g = Graph(session)
    ds, m = g.by_key("dataset:CNP9990004"), g.by_alias("method", "LeafSeg")
    assert "id" not in ds.attrs and "id" not in m.attrs
    idea = g.by_alias("idea", "用核染色先验约束其他基于 bin 的空间平台的细胞分配。")
    topic = g.by_alias("topic", "spatial domain detection")
    assert idea.attrs["transfer"]["to"] == "spatial domain detection" and idea.attrs["transfer"]["to_key"] == topic.key
    assert "t2" not in entity_text(idea) and g.edge(idea, topic, "applicable_to") is not None


def test_human_merge_target_that_comes_later_is_not_lost(library, session, settings):
    from rhizome.pipeline.canonicalize import free_key

    _lenient(settings)
    g = Graph(session)
    base = "Constrain GRN edges with ATAC priors when expression is sparse"
    a_text, b_text, decoy_text = base + " indeed", base, base + " also"
    a_key, b_key = free_key("idea", a_text), free_key("idea", b_text)
    session.add(HumanDecision(op="merge", payload={"from": a_key, "into": b_key}))
    session.flush()
    g.invalidate_redirects()
    assert g.pinned_by_decision(a_key) and g.pinned_by_decision(b_key)
    # a near-duplicate exists that would otherwise swallow A; the human said A belongs to B
    decoy = resolve_free(g, "idea", decoy_text).entity
    r = resolve_free(g, "idea", a_text)
    assert r.created and r.entity.key == a_key and r.entity.id != decoy.id
    b = resolve_free(g, "idea", b_text, attrs={"origin": "model"}).entity
    assert b.key == b_key and b.id not in (decoy.id, r.entity.id)
    skipped: list = []
    decisions.apply_all(g, skipped)
    assert not skipped and g.by_alias("idea", a_text).id == b.id and session.get(type(b), r.entity.id) is None
    # a decision whose source already went elsewhere is reported, not counted as applied
    session.add(HumanDecision(op="merge", payload={"from": a_key, "into": decoy.key}))
    session.flush()
    g.invalidate_redirects()
    skipped = []
    decisions.apply_all(g, skipped)
    assert any("ended up in" in x["reason"] for x in skipped)
    assert session.execute(select(Edge).where(Edge.src == -1)).first() is None
