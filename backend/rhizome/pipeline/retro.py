# SPDX-License-Identifier: Apache-2.0
"""Retro-tagging when a new topic is defined.

1. topic definition (+ examples) -> vector
2. kNN over all asset/work vectors -> top-k candidates (k bounds the cost, independent of library size)
3. reranker against the definition; drop below threshold
4. local LLM (if enabled) classifies about / applicable_to / none; confident results become an L1
   ``retro_tag`` extraction (so rebuilds replay them); everything else -> review queue
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import get_settings
from sqlalchemy import select

from ..db.models import Extraction, ReviewItem
from ..inference import get_backend
from ..ml import get_reranker
from .graph import Graph, embed_texts, entity_text, knn


def topic_definition(topic) -> str:
    a = topic.attrs or {}
    parts = [topic.canonical_name, a.get("definition") or ""]
    if a.get("examples"):
        parts.append("Examples: " + "; ".join(a["examples"]))
    if a.get("counter_examples"):
        parts.append("Not: " + "; ".join(a["counter_examples"]))
    return "\n".join(p for p in parts if p)


def retro_tag(s: Session, topic_key: str, k: int | None = None) -> dict:
    th = get_settings().thresholds
    g = Graph(s)
    topic = g.by_key(topic_key)
    if topic is None:
        raise LookupError(topic_key)
    definition = topic_definition(topic)
    qvec = embed_texts(s, [definition])[0]
    cands = knn(s, qvec, types=["work", "dataset", "method", "idea"], k=k or th.retro_k)
    ents = [g.by_id(i) for i, _ in cands]
    ents = [e for e in ents if e is not None and g.edge(e, topic, "about") is None
            and g.edge(e, topic, "applicable_to") is None]
    texts = [entity_text(e) for e in ents]
    rr = get_reranker()
    scores = [max(a, b) for a, b in zip(rr.score(definition, texts), rr.score(topic.canonical_name, texts))]
    kept = sorted(((e, sc) for e, sc in zip(ents, scores) if sc >= th.retro_rerank_min), key=lambda x: -x[1])
    backend = get_backend()
    auto: list[dict] = []
    queued = 0
    remaining = 0
    # without a local judge every candidate is a question for the user: only the best
    # retro_queue_max are queued per run; a rerun pages on (items already seen are skipped by key)
    seen = set(s.execute(select(ReviewItem.dedupe_key).where(ReviewItem.kind == "retro_tag",
                                                            ReviewItem.payload["topic"].as_string() == topic.key)).scalars())
    for e, sc in kept:
        if f"retro_tag:{topic.key}|{e.key}" in seen:
            continue
        verdict = backend.classify_topic_relation(entity_text(e), definition)
        if verdict is None and queued >= th.retro_queue_max:  # no local judge: the user is the judge
            remaining += 1
            continue
        if verdict and verdict[0] == "none" and verdict[1] >= th.retro_auto_conf:
            continue
        if verdict and verdict[0] != "none" and verdict[1] >= th.retro_auto_conf:
            relation = verdict[0] if e.type == "work" else "applicable_to"
            auto.append({"key": e.key, "relation": relation, "confidence": verdict[1]})
            continue
        item = g.queue("retro_tag", {"topic": topic.key, "topic_name": topic.canonical_name, "key": e.key,
                                     "name": e.canonical_name[:300], "type": e.type,
                                     "suggestion": verdict[0] if verdict else None, "score": round(sc, 4)},
                       dedupe=f"retro_tag:{topic.key}|{e.key}", score=sc)
        queued += item is not None
    if auto:
        ex = Extraction(kind="retro_tag", work_key=None, tier=1, model=backend.name, prompt_version="retro-v1",
                        schema_version="retro-v1", input_hashes=[], output={"topic": topic.key, "assignments": auto},
                        meta={})
        s.add(ex)
        s.flush()
        from .materialize import materialize_retro

        materialize_retro(g, ex)
    return {"candidates": len(cands), "after_rerank": len(kept), "auto": len(auto), "queued": queued,
            "remaining": remaining}
