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

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from ..config import get_settings
from ..db.models import Entity
from ..external.organisms import SCIENTIFIC
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


def is_user(e: Entity | None) -> bool:
    return bool(e is not None and (e.attrs or {}).get("origin") == "user")


def resolve_free(g: Graph, etype: str, name: str, *, attrs: dict[str, Any] | None = None,
                 status: str = "active", aliases: tuple[str, ...] = (), auto_merge: bool = True,
                 origin: str | None = None) -> Resolution:
    """``auto_merge=False``: even a very close neighbour only produces a merge *review* item (used
    for claims without NLI, where similar wording can still mean the opposite). ``origin``
    ("user" | "model"): a user's idea and a model's are never merged automatically either, so the
    user's wording is not silently replaced (and a model's idea not silently relabelled as theirs)."""
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
        g.fill_attrs(hit, attrs)
        return Resolution(hit, False, 1.0)
    key = free_key(etype, name)
    existing = g.by_key(key)  # may follow a human merge redirect
    if existing is not None:
        g.add_alias(existing, name, source="redirect" if existing.key != key else "extraction")
        g.fill_attrs(existing, attrs)
        return Resolution(existing, False, 1.0)
    if g.pinned_by_decision(key):  # the other side of a human merge comes later in this rebuild
        return Resolution(g.create(etype, key, name, status=status, attrs=attrs, aliases=aliases), True, None)

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
    cross_origin = best is not None and origin is not None and is_user(best[0]) != (origin == "user")
    if best and best[1] >= th.merge_auto and auto_merge and not cross_origin:
        g.add_alias(best[0], name, source="auto-merge")
        for a in aliases:
            g.add_alias(best[0], a)
        g.fill_attrs(best[0], attrs)
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


_NEGATIONS = {"not", "no", "never", "none", "cannot", "without", "fails", "fail", "neither", "nor"}
# Chinese has no spaces: match negation words leftmost-longest, after removing words that only
# look negative (不同 "different", 无监督 "unsupervised", 非常 "very", ...).
_ZH_NEG = re.compile(r"并非|并不|并未|没有|不是|不能|不会|不再|未能|未曾|无法|从未|从不|毫无|不|没|未|无|非|勿")
_ZH_NOT_NEG = ("不同", "不仅", "不断", "不久", "不少", "不同于", "不论", "不管", "无论", "无监督", "无标记",
               "无偏", "非常", "非编码", "非线性", "非参数", "非模式", "非洲", "未来", "无线", "不对称")
# opposite directions that keep almost the same wording (and so the same embedding)
_ANTONYMS = [("increase", "decrease"), ("increases", "decreases"), ("increased", "decreased"),
             ("higher", "lower"), ("up-regulates", "down-regulates"), ("upregulates", "downregulates"),
             ("upregulated", "downregulated"), ("activates", "represses"), ("activates", "inhibits"),
             ("promotes", "inhibits"), ("promotes", "suppresses"), ("enhances", "reduces"),
             ("precede", "follow"), ("precedes", "follows"), ("before", "after"), ("positive", "negative"),
             ("gain", "loss"), ("more", "less"),
             ("增加", "减少"), ("升高", "降低"), ("上调", "下调"), ("促进", "抑制"), ("激活", "抑制"),
             ("先于", "晚于"), ("早于", "晚于"), ("高于", "低于"), ("正相关", "负相关"), ("增强", "减弱")]


def _negated(text: str) -> bool:
    from ..text import tokens

    t = tokens(text.replace("n't", " not"))
    en = sum(1 for x in t if x in _NEGATIONS)
    zh_text = text
    for w in _ZH_NOT_NEG:
        zh_text = zh_text.replace(w, " ")
    return (en + len(_ZH_NEG.findall(zh_text))) % 2 == 1


def _words(text: str) -> set[str]:
    from ..text import tokens

    return set(tokens(text.lower()))


def _has(text: str, words: set[str], w: str) -> bool:
    return w in words if w.isascii() else w in text


def opposite_polarity(a: str, b: str) -> bool:
    """Near-identical claims that likely say the opposite: negation parity differs, or one uses a
    direction word whose antonym the other uses."""
    if _negated(a) != _negated(b):
        return True
    wa, wb = _words(a), _words(b)
    for x, y in _ANTONYMS:
        ax, ay, bx, by = _has(a, wa, x), _has(a, wa, y), _has(b, wb, x), _has(b, wb, y)
        if (ax and not ay and by and not bx) or (ay and not ax and bx and not by):
            return True
    return False


def resolve_claim(g: Graph, text: str, attrs: dict[str, Any]) -> ClaimResolution:
    nli = get_nli()
    if nli is None:
        # Fallback without NLI: a near-identical claim with opposite polarity is a contradiction
        # candidate (review queue), never a merge candidate; other close claims are never merged
        # automatically either (similar wording can still mean something else), only queued.
        th = get_settings().thresholds
        if g.by_alias("claim", text) is None:
            qvec = embed_texts(g.s, [text])[0]
            cands = [g.by_id(i) for i, _ in knn(g.s, qvec, types=["claim"], k=5)]
            cands = [c for c in cands if c is not None and opposite_polarity(c.canonical_name, text)]
            if cands:
                scores = get_reranker().score(text, [c.canonical_name for c in cands])
                flagged = [(c, sc) for c, sc in zip(cands, scores) if sc >= th.merge_review]
                if flagged:
                    e = g.by_key(free_key("claim", text)) or g.create("claim", free_key("claim", text), text,
                                                                      attrs=attrs)
                    return ClaimResolution(e, True, [], flagged)
        r = resolve_free(g, "claim", text, attrs=attrs, auto_merge=False)
        return ClaimResolution(r.entity, r.created, [], [])
    hit = g.by_alias("claim", text) or g.by_key(free_key("claim", text))
    if hit:
        return ClaimResolution(hit, False, [], [])
    if g.pinned_by_decision(free_key("claim", text)):
        return ClaimResolution(g.create("claim", free_key("claim", text), text, attrs=attrs), True, [], [])
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


def resolve_organism(g: Graph, name: str, taxa: dict[str, str] | None = None) -> Entity:
    """Never goes online: the taxid comes from the extraction (resolved at ingest), the built-in
    table, or what the library knew before a rebuild (older extractions without taxa)."""
    from ..external.organisms import lookup_builtin

    hit = g.by_alias("organism", name)
    if hit:
        return hit
    tid = (taxa or {}).get(name)
    if not (tid and tid.isdigit()):
        tid = lookup_builtin(name) or g.taxa_fallback.get(norm(name))
    if tid:
        key = f"organism:taxon:{tid}"
        e = g.by_key(key)
        if e is None:
            e = g.create("organism", key, SCIENTIFIC.get(tid, name.strip()), external_id=f"taxon:{tid}", embed=False)
        g.add_alias(e, name)
        return e
    n = norm(name)
    return g.by_key(f"organism:{n}") or g.create("organism", f"organism:{n}", name.strip(), embed=False)
