# SPDX-License-Identifier: Apache-2.0
"""The embedder belongs to the library; claims are not merged blindly without NLI; the synthesis
bar is bounded and resettable."""


import pytest
from fastapi.testclient import TestClient

from rhizome.pipeline.canonicalize import _negated, opposite_polarity


def test_chinese_negation_and_false_friends():
    assert _negated("染色质开放并非先于基因表达变化") and _negated("细胞中没有检测到该转录因子")
    assert not _negated("该方法用于无监督聚类") and not _negated("不同物种之间保守")
    assert not _negated("非编码 RNA 调控根发育")


def test_antonyms_count_as_opposite_polarity():
    assert opposite_polarity("Accessibility changes precede expression changes",
                             "Accessibility changes follow expression changes")
    assert opposite_polarity("该因子促进根伸长", "该因子抑制根伸长")
    assert not opposite_polarity("TCP factors promote elongation", "TCP factors promote root elongation")


def test_claims_are_never_auto_merged_without_nli(library, session):
    from sqlalchemy import select

    from rhizome.db.models import ReviewItem
    from rhizome.pipeline.canonicalize import resolve_claim
    from rhizome.pipeline.graph import Graph

    g = Graph(session)
    base = "TCP transcription factors gate the meristem-to-elongation transition in Arabidopsis roots."
    near = "TCP transcription factors gate the meristem-to-elongation transition in Arabidopsis root tips."
    r = resolve_claim(g, near, {})
    assert r.created and r.entity.key != g.by_alias("claim", base).key  # a new node, not merged ...
    items = session.execute(select(ReviewItem).where(ReviewItem.kind == "merge")).scalars().all()
    assert any(r.entity.key in (i.payload["a"], i.payload["b"]) for i in items)  # ... but reviewed


def test_library_indexed_with_another_model_is_reported(library, session, settings, monkeypatch):
    from rhizome.ml import IndexStale, registry
    from rhizome.pipeline.graph import embed_texts, index_model, knn

    session.commit()
    indexed = index_model(session)
    assert indexed  # recorded on first index

    class Other:
        name, dim = "other-embedder", 64

        def embed(self, texts):
            import numpy as np

            return np.ones((len(texts), 64), dtype=np.float32) / 8

    monkeypatch.setattr(registry, "get_embedder", lambda: Other())
    monkeypatch.setattr("rhizome.pipeline.graph.get_embedder", lambda: Other())
    with pytest.raises(IndexStale, match=indexed):
        knn(session, embed_texts(session, ["x"], persist=False)[0], k=3)


def test_rebuild_adopts_the_configured_model(library, session):
    from rhizome.db.models import KV
    from rhizome.pipeline.graph import INDEX_MODEL_KEY
    from rhizome.pipeline.rebuild import rebuild

    session.commit()
    rebuild(session, backup=False)
    from rhizome.ml import get_embedder

    assert session.get(KV, INDEX_MODEL_KEY).v["model"] == get_embedder().name


def test_switching_embedder_in_the_app_queues_a_rebuild(settings, monkeypatch):
    from rhizome.api.app import create_app
    from rhizome.ml import registry

    monkeypatch.setattr(registry, "_importable", lambda m: True)
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    assert c.patch("/settings", json={"language": "zh_CN"}).json()["rebuild_job"] is None
    r = c.patch("/settings", json={"embedder": "bge-m3"}).json()
    assert r["rebuild_job"]
    assert c.get(f"/jobs/{r['rebuild_job']}").json()["kind"] == "rebuild"


def test_synthesis_bar_is_bounded_per_model_and_resettable(settings, session):
    from rhizome.config import get_settings
    from rhizome.services import synthesis

    base = get_settings().thresholds.synthesis_sim
    for _ in range(200):
        synthesis.tune_threshold(session, useful=False)
    assert synthesis.current_threshold(session) == pytest.approx(base + synthesis.MAX_DELTA)
    for _ in range(3):
        synthesis.tune_threshold(session, useful=True)
    assert synthesis.current_threshold(session) < base + synthesis.MAX_DELTA
    assert synthesis.reset_threshold(session) == pytest.approx(base)
    # 1 useful in 5 keeps the bar where it is
    for _ in range(10):
        synthesis.tune_threshold(session, useful=True)
        for _ in range(4):
            synthesis.tune_threshold(session, useful=False)
    assert synthesis.current_threshold(session) == pytest.approx(base, abs=0.011)
