# SPDX-License-Identifier: Apache-2.0
"""Entity canonicalisation.

Anchored entities (Work, Dataset with accession, Method with repo, Organism, Modality) align by
external ID. Free concepts (Topic, Idea, Claim, Method without repo, Dataset without accession)
go through: exact alias -> vector top-5 -> reranker -> thresholds
(>= merge_auto: merge; [merge_review, merge_auto): new entity + review item; below: new entity).
Claims use NLI when enabled (bidirectional entailment = same; one-way = support; contradiction ->
review queue, never auto-written until calibrated).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from ..config import get_settings
from ..db.models import Entity
from ..external.organisms import SCIENTIFIC
from ..external.verify import taxonomy_id
from ..ml import get_nli, get_reranker
from ..text import norm, sha256
from .graph import Graph, embed_texts, entity_text, knn


@dataclass
class Resolution:
    entity: Entity
    created: bool
    score: float | None = None  # best similarity to an existing entity when created


def free_key(etype: str, name: str) -> str:
    n = norm(name)
    if etype in ("idea", "claim") or len(n) > 120:
        return f"{etype}:{sha256(n)[:20]}"
    return f"{etype}:{n}"


def resolve_free(g: Graph, etype: str, name: str, *, attrs: dict[str, Any] | None = None,
                 status: str = "active", aliases: tuple[str, ...] = ()) -> Resolution:
    th = get_settings().thresholds
    hit = g.by_alias(etype, name)
    if hit is None:
        for a in aliases:
            hit = g.by_alias(etype, a)
            if hit:
                break
    if hit is not None:
        for a in aliases:
            g.add_alias(hit, a)
        return Resolution(hit, False, 1.0)
    key = free_key(etype, name)
    existing = g.by_key(key)  # may follow a human merge redirect
    if existing is not None:
        g.add_alias(existing, name, source="redirect" if existing.key != key else "extraction")
        return Resolution(existing, False, 1.0)

    probe = Entity(type=etype, key=key, canonical_name=name, attrs=attrs or {})
    probe_text = entity_text(probe)
    qvec = embed_texts(g.s, [probe_text])[0]
    neighbours = knn(g.s, qvec, types=[etype], k=5)
    best: tuple[Entity, float] | None = None
    if neighbours:
        cands = [g.by_id(i) for i, _ in neighbours]
        cands = [c for c in cands if c is not None]
        scores = get_reranker().score(probe_text, [entity_text(c) for c in cands])
        j = max(range(len(cands)), key=lambda i: scores[i])
        best = (cands[j], scores[j])
    if best and best[1] >= th.merge_auto:
        g.add_alias(best[0], name, source="auto-merge")
        for a in aliases:
            g.add_alias(best[0], a)
        return Resolution(best[0], False, best[1])

    e = g.create(etype, key, name, status=status, attrs=attrs, aliases=aliases)
    if best and best[1] >= th.merge_review and frozenset((e.key, best[0].key)) not in g.distinct_pairs():
        a, b = sorted([e.key, best[0].key])
        g.queue("merge", {"type": etype, "a": e.key, "b": best[0].key, "a_name": e.canonical_name,
                          "b_name": best[0].canonical_name, "score": round(best[1], 4)},
                dedupe=f"merge:{a}|{b}", score=best[1])
    return Resolution(e, True, best[1] if best else None)


# ---- claims -------------------------------------------------------------------------

@dataclass
class ClaimResolution:
    entity: Entity
    created: bool
    also_supports: list[Entity]
    contradicts: list[tuple[Entity, float]]


_NEGATIONS = {"not", "no", "never", "none", "cannot", "without", "fails", "fail", "neither", "nor",
              "不", "没有", "无", "未", "并非", "不能"}


def _negated(text: str) -> bool:
    from ..text import tokens

    t = tokens(text.replace("n't", " not"))
    return sum(1 for x in t if x in _NEGATIONS) % 2 == 1


def resolve_claim(g: Graph, text: str, attrs: dict[str, Any]) -> ClaimResolution:
    nli = get_nli()
    if nli is None:
        # Fallback without NLI: a near-identical claim with opposite negation is a contradiction
        # candidate (review queue), never a merge candidate.
        th = get_settings().thresholds
        if g.by_alias("claim", text) is None:
            qvec = embed_texts(g.s, [text])[0]
            cands = [g.by_id(i) for i, _ in knn(g.s, qvec, types=["claim"], k=5)]
            cands = [c for c in cands if c is not None and _negated(c.canonical_name) != _negated(text)]
            if cands:
                scores = get_reranker().score(text, [c.canonical_name for c in cands])
                flagged = [(c, sc) for c, sc in zip(cands, scores) if sc >= th.merge_review]
                if flagged:
                    e = g.by_key(free_key("claim", text)) or g.create("claim", free_key("claim", text), text,
                                                                      attrs=attrs)
                    return ClaimResolution(e, True, [], flagged)
        r = resolve_free(g, "claim", text, attrs=attrs)
        return ClaimResolution(r.entity, r.created, [], [])
    hit = g.by_alias("claim", text) or g.by_key(free_key("claim", text))
    if hit:
        return ClaimResolution(hit, False, [], [])
    qvec = embed_texts(g.s, [text])[0]
    supports: list[Entity] = []
    contradicts: list[tuple[Entity, float]] = []
    for cid, _sim in knn(g.s, qvec, types=["claim"], k=5):
        cand = g.by_id(cid)
        if cand is None:
            continue
        fwd, pf = nli.classify(text, cand.canonical_name)
        bwd, pb = nli.classify(cand.canonical_name, text)
        if fwd == "entailment" and bwd == "entailment":
            g.add_alias(cand, text, source="nli-merge")
            return ClaimResolution(cand, False, [], [])
        if fwd == "entailment":
            supports.append(cand)
        elif fwd == "contradiction" or bwd == "contradiction":
            contradicts.append((cand, max(pf, pb)))
    e = g.create("claim", free_key("claim", text), text, attrs=attrs)
    return ClaimResolution(e, True, supports, contradicts)


# ---- anchored -----------------------------------------------------------------------

@lru_cache(maxsize=1)
def _modalities() -> list[dict]:
    return yaml.safe_load(resources.files("rhizome").joinpath("data", "modalities.yaml").read_text("utf-8"))


def resolve_modality(g: Graph, name: str) -> Entity:
    hit = g.by_alias("modality", name)
    if hit:
        return hit
    n = norm(name)
    for m in _modalities():
        names = [m["name"], *(m.get("aliases") or [])]
        if n in {norm(x) for x in names}:
            key = f"modality:{norm(m['name'])}"
            e = g.by_key(key) or g.create("modality", key, m["name"], external_id=m.get("edam") or None,
                                          aliases=tuple(names[1:]), embed=False)
            g.add_alias(e, name)
            return e
    return g.by_key(f"modality:{n}") or g.create("modality", f"modality:{n}", name.strip(), embed=False)


def resolve_organism(g: Graph, name: str) -> Entity:
    hit = g.by_alias("organism", name)
    if hit:
        return hit
    tid = taxonomy_id(name)
    if tid:
        key = f"organism:taxon:{tid}"
        e = g.by_key(key)
        if e is None:
            e = g.create("organism", key, SCIENTIFIC.get(tid, name.strip()), external_id=f"taxon:{tid}", embed=False)
        g.add_alias(e, name)
        return e
    n = norm(name)
    return g.by_key(f"organism:{n}") or g.create("organism", f"organism:{n}", name.strip(), embed=False)
