# SPDX-License-Identifier: Apache-2.0
from datetime import timedelta

from rhizome import jobs
from rhizome.db.models import Entity, ReviewItem, utcnow
from rhizome.pipeline.graph import Graph
from rhizome.services.cards import due_cards, grade
from rhizome.services.review import list_items, resolve
from rhizome.services.vocab import export_vocab


def test_fsrs_grading_and_daily_caps(library, session, settings):
    cards = due_cards(session, 50)
    assert cards and cards[0]["priority"] >= 10  # user's own idea first
    r = grade(session, cards[0]["id"], 3)
    assert r["interval_days"] > 0
    settings.review_daily_new = 1
    session.flush()
    assert len(due_cards(session, 50)) == 0  # one new card already introduced today
    later = due_cards(session, 50, now=utcnow() + timedelta(days=30))
    assert any(c["id"] == cards[0]["id"] for c in later)


def test_review_queue_resolution_writes_decisions(library, session):
    items = list_items(session)["items"]
    contra = next(i for i in items if i["kind"] == "contradiction")
    assert contra["context"]["claim"]["connections"]
    out = resolve(session, contra["id"], "accept")
    assert out["decision_id"]
    g = Graph(session)
    w2 = g.by_key("work:doi:10.5555/rhz.example.0002")
    claim = g.by_key(contra["payload"]["claim"])
    assert g.edge(w2, claim, "contradicts").status == "confirmed"


def test_retro_tagging_queues_candidates(library, session):
    from rhizome.pipeline import decisions
    from rhizome.pipeline.retro import retro_tag

    decisions.record(Graph(session), "create_topic", {"name": "chromatin accessibility",
                                                      "definition": "Open chromatin assays such as ATAC-seq"})
    out = retro_tag(session, "topic:chromatin accessibility", k=50)
    assert out["queued"] >= 1 and out["auto"] == 0
    item = next(i for i in list_items(session, "retro_tag")["items"])
    resolve(session, item["id"], "applicable_to")
    g = Graph(session)
    e = g.by_key(item["payload"]["key"])
    rel = "applicable_to"
    assert g.edge(e, g.by_key("topic:chromatin accessibility"), rel) is not None or e.type == "work"


def test_retro_tagging_with_local_model_writes_l1(library, session, monkeypatch):
    from rhizome.db.models import Extraction
    from rhizome.pipeline import decisions, retro
    from rhizome.pipeline.rebuild import rebuild

    class Fake:
        name = "fake-llm"

        def classify_topic_relation(self, a, t):
            return ("applicable_to", 0.95)

        def judge_breadth(self, a, b):
            return None

        def make_card(self, a):
            return None

    monkeypatch.setattr(retro, "get_backend", lambda: Fake())
    decisions.record(Graph(session), "create_topic", {"name": "chromatin accessibility",
                                                      "definition": "Open chromatin assays such as ATAC-seq"})
    out = retro.retro_tag(session, "topic:chromatin accessibility", k=50)
    assert out["auto"] >= 1
    assert session.query(Extraction).filter(Extraction.kind == "retro_tag").count() == 1
    session.commit()
    rebuild(session, backup=False)  # replayed from L1
    t = Graph(session).by_key("topic:chromatin accessibility")
    from rhizome.db.models import Edge

    assert session.query(Edge).filter(Edge.dst == t.id).count() >= 1


def test_nightly_job_and_synthesis(library, session, settings):
    settings.thresholds.synthesis_sim = 0.1
    j = jobs.enqueue(session, "nightly", {"force_synthesis": True})
    session.commit()
    jobs.run_all()
    session.expire_all()
    from rhizome.db.models import Job

    job = session.get(Job, j.id)
    assert job.status == "done", job.error
    assert job.result["communities"] >= 1
    # the three example papers form one community; drop that criterion to exercise pair generation
    from rhizome.db.models import KV
    from rhizome.services.synthesis import generate_candidates

    session.get(KV, "communities").v = {}
    generate_candidates(session)
    syn = session.query(ReviewItem).filter(ReviewItem.kind == "synthesis").all()
    assert syn
    for it in syn:  # never proposes pairs already linked within two hops
        from rhizome.services.synthesis import _two_hop

        g = Graph(session)
        assert g.by_key(it.payload["b"]).id not in _two_hop(session, g.by_key(it.payload["a"]).id)
    from rhizome.services.synthesis import weekly_digest

    d = weekly_digest(session)
    assert d["pairs"] and d["contradictions_pending_review"]
    resolve(session, d["pairs"][0]["item_id"], "useful", "Both denoise sparse measurements with neighbours.")
    assert session.query(Entity).filter(Entity.canonical_name.like("Both denoise%")).count() == 1


def test_topic_promotion(library, session, settings):
    from rhizome.pipeline.materialize import promote_topics

    settings.thresholds.topic_promote_works = 2
    g = Graph(session)
    promote_topics(g)
    assert g.by_key("topic:spatial domain detection").status == "active"  # 2 works
    assert g.by_key("topic:benchmarking").status == "candidate"


def test_vocab_export(library, session):
    from rhizome.pipeline import decisions

    decisions.record(Graph(session), "create_topic", {"name": "GRN inference", "aliases": ["基因调控网络推断"]})
    text = export_vocab(session)
    assert "name: GRN inference" in text and "基因调控网络推断" in text
    assert "benchmarking" not in text  # candidates are excluded
