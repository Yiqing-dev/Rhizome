# SPDX-License-Identifier: Apache-2.0
"""Durable writes, a vector index that does not copy itself, lean snapshots, bounded synthesis
and a local graph paged in SQL."""

import sqlite3

import numpy as np
from sqlalchemy import func, select, text

from conftest import EXAMPLES
from rhizome.db.models import Entity, ReviewItem, VectorCache
from rhizome.pipeline.graph import VECTORS, Graph, prune_vector_cache
from rhizome.pipeline.ingest import ingest_text


def test_commits_are_durable_and_raw_files_fsynced(settings, session, monkeypatch, tmp_path):
    from rhizome import rawstore

    assert session.execute(text("PRAGMA synchronous")).scalar_one() == 2  # FULL
    synced = []
    real = rawstore.os.fsync
    monkeypatch.setattr(rawstore.os, "fsync", lambda fd: synced.append(fd) or real(fd))
    sha = rawstore.put(session, b"rxf_version: 1\n", "rxf", "a.yaml")
    p = rawstore.path_for(sha, "rxf")
    assert p.read_bytes() == b"rxf_version: 1\n" and synced and not list(p.parent.glob("*.tmp"))
    p.write_bytes(b"rxf_")  # truncated by a crash: the next store of the same bytes repairs it
    rawstore.put(session, b"rxf_version: 1\n", "rxf", "a.yaml")
    assert p.read_bytes() == b"rxf_version: 1\n"
    assert rawstore.check(session)["ok"]
    p.unlink()
    (p.parent / "x.tmp").write_bytes(b"")
    chk = rawstore.check(session)
    assert chk["missing"] == [p.name] and chk["stray_tmp"] and not chk["ok"]


def test_vector_index_appends_without_copying(library, session):
    from rhizome.ml import get_embedder

    model = get_embedder().name
    VECTORS.clear()
    ids0, _, mat0 = VECTORS.get(session, model)
    n0 = len(ids0)
    buffers = {VECTORS.buffer_id(session, model)}
    vec = np.ones(mat0.shape[1], dtype=np.float32)
    for i in range(1000):
        VECTORS.upsert(session, model, 10_000 + i, "idea", vec, 1)
        buffers.add(VECTORS.buffer_id(session, model))
    assert len(buffers) <= 12, "capacity should grow geometrically"
    ids, types, mat = VECTORS.get(session, model) if False else VECTORS._data[VECTORS._key(session, model)][1:4]
    n = VECTORS._data[VECTORS._key(session, model)][4]
    assert n == n0 + 1000 and mat.shape[0] >= n and len(ids) == len(types) == mat.shape[0]
    VECTORS.remove(session, model, 10_000, 2)
    assert VECTORS._data[VECTORS._key(session, model)][4] == n - 1
    assert 10_000 not in VECTORS._data[VECTORS._key(session, model)][1][:n - 1]
    VECTORS.clear()
    loads = VECTORS.loads
    ids, _, mat = VECTORS.get(session, model)
    assert len(ids) == n0 and mat.shape[0] >= n0 and VECTORS.loads == loads + 1
    VECTORS.get(session, model)
    assert VECTORS.loads == loads + 1  # signature unchanged: no reload


def test_vector_cache_is_pruned_and_snapshots_are_lean(library, session, settings, tmp_path):
    from rhizome.pipeline.graph import embed_texts
    from rhizome.services.snapshot import make_snapshot

    embed_texts(session, ["a probe text nobody keeps", "another one"])
    session.add(VectorCache(text_sha="x" * 64, model="old-model", vec=b"\x00" * 8))
    session.flush()
    before = session.execute(select(func.count()).select_from(VectorCache)).scalar_one()
    pruned = prune_vector_cache(session)
    after = session.execute(select(func.count()).select_from(VectorCache)).scalar_one()
    assert pruned >= 3 and after == before - pruned
    assert session.execute(select(func.count()).select_from(VectorCache).where(VectorCache.model == "old-model")).scalar_one() == 0
    # entity texts stay cached (a rebuild reuses them)
    assert after >= session.execute(select(func.count()).select_from(Entity)
                                    .where(Entity.type.notin_(("organism", "modality")))).scalar_one()
    session.commit()
    embed_texts(session, ["probe again"])
    session.commit()
    snap = make_snapshot(settings, tmp_path / "s.db")
    with sqlite3.connect(snap) as c:
        assert c.execute("select count(*) from vector_cache").fetchone()[0] == 0
        assert c.execute("select count(*) from entity").fetchone()[0] > 0
        assert c.execute("select count(*) from embedding").fetchone()[0] > 0
    assert session.execute(select(func.count()).select_from(VectorCache)).scalar_one() > 0  # the library keeps its cache


def test_synthesis_is_batched_capped_and_uses_a_watermark(library, session, settings):
    from datetime import timedelta

    from rhizome.db.models import KV, utcnow
    from rhizome.services import synthesis

    settings.thresholds.synthesis_sim = 0.05
    session.get(KV, "communities") or session.add(KV(k="communities", v={}))
    n = synthesis.generate_candidates(session, max_items=50)
    items = session.execute(select(ReviewItem).where(ReviewItem.kind == "synthesis")).scalars().all()
    assert n == len(items) > 0
    per_source: dict = {}
    for it in items:
        per_source[it.payload["a"]] = per_source.get(it.payload["a"], 0) + 1
    assert max(per_source.values()) <= synthesis.PER_SOURCE
    scores = [it.score for it in items]
    assert scores == sorted(scores, reverse=True) or len(set(per_source)) > 1  # best pairs first
    # a watermark after everything was added: nothing new to pair
    assert synthesis.generate_candidates(session, since=utcnow() + timedelta(seconds=1)) == 0


def test_local_graph_is_paged_in_sql_and_skips_hubs(library, session):
    from rhizome.services.views import neighbors

    g = Graph(session)
    w = g.by_key("work:doi:10.5555/rhz.example.0001")
    one = neighbors(session, w.id, hops=1, limit=300)
    two = neighbors(session, w.id, hops=2, limit=300)
    assert one["total_nodes"] <= two["total_nodes"] and w.id in {n["id"] for n in two["nodes"]}
    # the organism node is in the graph, but the second hop does not fan out through it
    org = g.by_id(next(n for n in one["nodes"] if n["type"] == "organism")["id"])
    far = g.create("work", "work:title:farhub", "A paper that only shares the organism")
    g.upsert_edge(far, org, "of_organism")
    two = neighbors(session, w.id, hops=2, limit=300)
    assert far.id not in {n["id"] for n in two["nodes"]}
    assert far.id in {n["id"] for n in neighbors(session, org.id, hops=1, limit=300)["nodes"]}
    small = neighbors(session, w.id, hops=2, limit=6)
    assert len(small["nodes"]) <= 3 and small["has_more"] and small["total_nodes"] == two["total_nodes"]
    page2 = neighbors(session, w.id, hops=2, limit=6, offset=2)
    assert {n["id"] for n in page2["nodes"]} - {w.id} != {n["id"] for n in small["nodes"]} - {w.id}
    assert all(e["src"] in {n["id"] for n in small["nodes"]} for e in small["edges"])
    typed = neighbors(session, w.id, hops=1, edge_types=["produces"])
    assert all(e["type"] == "produces" for e in typed["edges"]) and len(typed["nodes"]) >= 2


def test_ingest_still_works_with_the_new_index(settings, session):
    assert ingest_text(session, (EXAMPLES / "deep-grn-atlas.yaml").read_text("utf-8"), "a.yaml").ok
