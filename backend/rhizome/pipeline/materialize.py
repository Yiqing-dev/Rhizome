# SPDX-License-Identifier: Apache-2.0
"""L1 -> L2/L3: turn current extraction rows into entities and edges (deterministic, replayable)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update

from ..config import get_settings
from ..db.models import Edge, Entity, Extraction, ReviewCard, Work, utcnow
from ..external.ids import effective_database, normalize_doi, normalize_repo, normalize_zenodo
from ..i18n import _
from ..inference import get_backend
from ..rxf.schema import RxfDocument, parse_stored, uses_ids
from ..text import norm, sha256
from .canonicalize import is_user, resolve_claim, resolve_free, resolve_modality, resolve_organism
from ..ml import get_reranker
from .graph import Graph, embed_texts, knn

log = logging.getLogger(__name__)


@dataclass
class Materialized:
    work: Entity
    assets: list[Entity] = field(default_factory=list)
    topics: list[Entity] = field(default_factory=list)


# ---- works ---------------------------------------------------------------------------

def work_key(openalex_id: str | None, doi: str | None, title: str) -> str:
    if openalex_id:
        return f"work:openalex:{openalex_id}"
    if doi:
        return f"work:doi:{normalize_doi(doi)}"
    return f"work:title:{sha256(norm(title))[:20]}"


def resolve_work(g: Graph, *, openalex_id: str | None, doi: str | None, title: str,
                 year: int | None) -> Entity:
    """Find the Work node; preprint and published versions collapse into one node."""
    keys = [work_key(openalex_id, None, title)] if openalex_id else []
    if doi:
        keys.append(f"work:doi:{normalize_doi(doi)}")
    for k in keys:
        e = g.by_key(k)
        if e is not None and e.type != "work":  # a stale cross-type redirect; never fold a paper into it
            e = None
        if e:
            break
    else:
        e = None
        for alias in filter(None, [openalex_id, doi and normalize_doi(doi)]):
            e = g.by_alias("work", alias)
            if e:
                break
        if e is None:
            # same normalised title, year within one: preprint <-> journal version
            for cand in g.s.execute(
                select(Entity).join(Work, Work.entity_id == Entity.id)
                .where(Entity.type == "work", func.lower(Entity.canonical_name) == title.strip().lower())
            ).scalars():
                w = g.s.get(Work, cand.id)
                if w and (year is None or w.year is None or abs(w.year - year) <= 1):
                    e = cand
                    break
    if e is None:
        e = g.create("work", work_key(openalex_id, doi, title), title,
                     external_id=openalex_id or (normalize_doi(doi) if doi else None))
    w = g.s.get(Work, e.id)
    if doi:
        g.add_alias(e, normalize_doi(doi), source="doi")
        if normalize_doi(doi) not in (w.dois or []):
            w.dois = sorted(set(w.dois or []) | {normalize_doi(doi)})
    if openalex_id:
        g.add_alias(e, openalex_id, source="openalex")
        if not w.openalex_id:
            clash = g.s.execute(select(Work).where(Work.openalex_id == openalex_id)).scalar_one_or_none()
            if clash is None:
                w.openalex_id = openalex_id
    g.add_alias(e, title)
    w.year = w.year or year
    return e


# ---- RXF -----------------------------------------------------------------------------

def _strength(evidence_type: str, logic_jump: bool) -> str:
    if logic_jump or evidence_type == "speculative":
        return "weak"
    return "strong" if evidence_type == "causal" else "medium"


def materialize_rxf(g: Graph, ex: Extraction) -> Materialized:
    g.clock = ex.created_at
    try:
        return _materialize_rxf(g, ex)
    finally:
        g.clock = None


def _materialize_rxf(g: Graph, ex: Extraction) -> Materialized:
    doc = parse_stored(ex.output, ex.schema_version)
    meta = ex.meta or {}
    checks: dict[str, str] = meta.get("checks", {})
    p = doc.paper
    work = resolve_work(g, openalex_id=meta.get("openalex_id"), doi=p.doi, title=p.title, year=p.year)
    w = g.s.get(Work, work.id)
    w.tier = max(w.tier or 0, ex.tier or 1)  # T2: a deep export with the PDF attached
    g.update_attrs(work, tldr=doc.tldr, paper_types=p.type, venue=p.venue, authors=p.authors, url=p.url,
                   depth="deep" if doc.depth == "deep" or work.attrs.get("depth") == "deep" else "light",
                   rxf_extraction=ex.id)
    out = Materialized(work=work)
    eid = ex.id
    # file-local id -> library entity (None when the item was not stored, e.g. a fabricated accession)
    ids: dict[str, Entity | None] = {}
    by_id = uses_ids(doc)

    def remember(item, ent: Entity | None) -> None:
        if item.id and item.id not in ids:
            ids[item.id] = ent

    def lookup(ref: str, types: tuple[str, ...]) -> Entity | None:
        """A reference: an id of this file, or (older exports without ids) a name in the library."""
        if by_id:
            return ids.get(ref)
        return next((x for x in (g.by_alias(t, ref) for t in types) if x), None)

    def edge(src, dst, etype, **kw):
        return g.upsert_edge(src, dst, etype, extraction_id=eid, **kw)

    for name in p.organisms:
        edge(work, resolve_organism(g, name, meta.get("taxa")), "of_organism")
    for name in p.modalities:
        edge(work, resolve_modality(g, name), "of_modality")

    for t in doc.topics:
        r = resolve_free(g, "topic", t.name, status="candidate", aliases=tuple(t.aliases))
        if r.created:
            _topic_relation_candidates(g, r.entity)
        edge(work, r.entity, t.relation)
        remember(t, r.entity)
        out.topics.append(r.entity)

    for c in doc.claims:
        attrs = {"evidence_type": c.evidence_type, "boundary": c.boundary}
        cr = resolve_claim(g, c.text, attrs)
        eattrs = {"evidence_type": c.evidence_type, "boundary": c.boundary, "logic_jump": c.logic_jump,
                  "strength": _strength(c.evidence_type, c.logic_jump)}
        edge(work, cr.entity, c.stance, evidence=c.evidence, attrs=eattrs,
             confidence=0.5 if c.logic_jump else 1.0)
        if c.stance == "supports":  # a paper that contradicts this claim does not support what it entails
            for other in cr.also_supports:
                edge(work, other, "supports", evidence=c.evidence, attrs={**eattrs, "via": "nli"}, confidence=0.7)
        for other, p_contra in cr.contradicts:
            g.queue("contradiction", {"work": work.key, "claim": other.key, "claim_text": other.canonical_name,
                                      "new_claim": cr.entity.key, "new_text": c.text, "evidence": c.evidence,
                                      "stance": c.stance, "evidence_type": c.evidence_type,
                                      "strength": eattrs["strength"], "extraction_id": eid,
                                      "score": round(p_contra, 4)},
                    dedupe=f"contradiction:{work.key}|{other.key}", score=p_contra)
        remember(c, cr.entity)
        out.assets.append(cr.entity)

    for d in doc.assets.datasets:
        ent = _dataset(g, d, checks, authoritative=d.role == "produces")
        remember(d, ent)
        if ent is None:
            continue
        edge(work, ent, d.role, evidence=d.evidence)
        if d.organism:
            edge(ent, resolve_organism(g, d.organism, meta.get("taxa")), "of_organism")
        if d.modality:
            edge(ent, resolve_modality(g, d.modality), "of_modality")
        out.assets.append(ent)

    for m in doc.assets.methods:
        ent = _method(g, m, checks, authoritative=m.role == "proposes")
        remember(m, ent)
        if ent is None:
            continue
        edge(work, ent, m.role, evidence=m.evidence)
        if m.modality:
            edge(ent, resolve_modality(g, m.modality), "of_modality")
        for parent in m.extends:
            pr = resolve_free(g, "method", parent)
            edge(ent, pr.entity, "extends")
        out.assets.append(ent)

    for idea in doc.assets.ideas:
        transfer, topic = None, None
        if idea.transfer:
            transfer = idea.transfer.model_dump()
            if idea.transfer.to:
                # the file-local id (t2) or name becomes the topic's canonical name and key
                topic = ids.get(idea.transfer.to) or g.by_alias("topic", idea.transfer.to)
                if topic is not None and topic.type == "topic":
                    transfer.update(to=topic.canonical_name, to_key=topic.key)
                elif by_id and idea.transfer.to in ids:
                    transfer["to"] = None  # an id of an item that was not stored
        attrs = {"origin": idea.origin, "transfer": transfer}
        r = resolve_free(g, "idea", idea.text, attrs=attrs, origin=idea.origin)
        if idea.origin == "user" and (r.created or is_user(r.entity)):
            g.update_attrs(r.entity, origin="user")
        if transfer and (r.entity.attrs or {}).get("transfer") != transfer and topic is not None:
            g.update_attrs(r.entity, transfer=transfer)
            g.reindex(r.entity)
        edge(work, r.entity, "proposes", attrs={"origin": idea.origin})
        remember(idea, r.entity)
        if topic is not None:
            edge(r.entity, topic, "applicable_to", attrs={"transfer_type": idea.transfer.type,
                                                          "barrier": idea.transfer.barrier})
        out.assets.append(r.entity)

    # user insights: create all first, so an insight may link to another insight of the same file
    insights = []
    for ins in doc.user_insights:
        r = resolve_free(g, "idea", ins.text, attrs={"origin": "user", "weight": 2.0}, origin="user")
        if r.created or is_user(r.entity):
            g.update_attrs(r.entity, origin="user", weight=2.0)
        edge(work, r.entity, "proposes", attrs={"origin": "user"}, confidence=1.0)
        remember(ins, r.entity)
        insights.append((ins, r.entity))
        out.assets.append(r.entity)
    for ins, me in insights:
        links: list[str] = []
        names: list[str] = []
        unresolved: list[str] = []
        for target in ins.links_to:
            hit = lookup(target, ("topic", "method", "dataset", "idea", "claim", "work"))
            if hit is None or hit.id == me.id:
                unresolved.append(target)
                continue
            links.append(hit.key)
            names.append(hit.canonical_name)
            # the user's own view joins the graph: to a topic as applicability, otherwise as a link
            edge(me, hit, "applicable_to" if hit.type == "topic" else "relates_to", attrs={"origin": "user"})
        g.update_attrs(me, links=sorted(set((me.attrs or {}).get("links", [])) | set(links)),
                       link_names=sorted(set((me.attrs or {}).get("link_names", [])) | set(names)),
                       links_unresolved=unresolved)
    # which entity each insight became, so the paper card can show and edit the current wording
    ex.meta = {**meta, "insight_keys": [me.key for _, me in insights]}

    if doc.depth == "deep":
        _make_cards(g, doc, work, out.assets, lookup)
    g.reindex(work)  # attrs (tldr, depth) were attached after create(); index the final text
    return out


def _dataset(g: Graph, d, checks: dict[str, str], authoritative: bool = False) -> Entity | None:
    attrs = {k: v for k, v in d.model_dump().items() if k not in ("id", "role", "evidence") and v is not None}
    if d.accession:
        raw_acc = d.accession.strip()
        if checks.get(raw_acc) in ("not_found", "bad_format"):
            return None
        db = effective_database(raw_acc, d.database)  # a mislabelled real id keeps its own database
        acc = f"zenodo.{normalize_zenodo(raw_acc)}" if db == "Zenodo" else raw_acc
        if db:
            attrs["database"] = db
        checks = {**checks, acc: checks.get(raw_acc, "unverified")}
        key = f"dataset:{acc}"
        e = g.by_key(key) or g.by_external_id("dataset", acc) or (g.by_alias("dataset", d.name) if d.name else None)
        if e is None:
            e = g.create("dataset", key, d.name or acc, external_id=acc,
                         attrs={**attrs, "verified": checks.get(acc, "unverified")},
                         aliases=(acc,) + ((d.name,) if d.name else ()))
        else:
            g.anchor(e, acc)
            g.merge_reported(e, attrs, authoritative=authoritative)
            g.add_alias(e, acc)
            if d.name:
                g.add_alias(e, d.name)
            g.reindex(e)
        return e
    return resolve_free(g, "dataset", d.name, attrs=attrs).entity


def _method(g: Graph, m, checks: dict[str, str], authoritative: bool = False) -> Entity | None:
    attrs = {k: v for k, v in m.model_dump().items()
             if k not in ("id", "role", "evidence", "extends") and v not in (None, [])}
    repo = normalize_repo(m.repo) if m.repo else None
    if m.repo and (repo is None or checks.get(repo) == "not_found"):
        # not a forge repository (a lab site, Hugging Face, ...) -> a free concept that keeps the
        # link; a repository the forge says does not exist -> the URL is dropped
        if repo is not None or checks.get(m.repo) != "unanchored":
            attrs.pop("repo", None)
        repo = None
    if repo:
        key = f"method:repo:{repo}"
        e = g.by_key(key) or g.by_external_id("method", repo) or g.by_alias("method", m.name)
        if e is None:
            e = g.create("method", key, m.name, external_id=repo,
                         attrs={**attrs, "verified": checks.get(repo, "unverified")}, aliases=(repo,))
        else:
            g.anchor(e, repo)
            g.merge_reported(e, attrs, authoritative=authoritative)
            g.add_alias(e, m.name)
            g.add_alias(e, repo)
            g.reindex(e)
        return e
    return resolve_free(g, "method", m.name, attrs=attrs).entity


def _topic_relation_candidates(g: Graph, topic: Entity) -> None:
    """Broader/narrower candidates for a new topic. Always reviewed, never auto-applied."""
    th = get_settings().thresholds
    qvec = embed_texts(g.s, [topic.canonical_name])[0]
    neighbours = [g.by_id(i) for i, _ in knn(g.s, qvec, types=["topic"], k=5, exclude={topic.id})]
    neighbours = [n for n in neighbours if n is not None]
    if not neighbours:
        return
    scores = get_reranker().score(topic.canonical_name, [n.canonical_name for n in neighbours])
    distinct = g.distinct_pairs()
    backend = get_backend()
    for other, sc in zip(neighbours, scores):
        if sc < th.topic_relation_min or sc >= th.merge_review or frozenset((topic.key, other.key)) in distinct:
            continue
        verdict = backend.judge_breadth(topic.canonical_name, other.canonical_name)
        suggestion = verdict[0] if verdict else None
        if verdict and verdict[0] == "none":
            continue
        a, b = sorted([topic.key, other.key])
        g.queue("topic_relation", {"a": topic.key, "b": other.key, "a_name": topic.canonical_name,
                                   "b_name": other.canonical_name, "suggestion": suggestion,
                                   "score": round(sc, 4)},
                dedupe=f"topic_relation:{a}|{b}", score=sc)


# ---- review cards ---------------------------------------------------------------------

GENERATED = ("template", "local_llm")


def card_id(entity_key: str, q: str, origin: str | None = None) -> str:
    """Cards written in an export are identified by their question. Generated cards (template,
    local model) have one slot per entity: their wording follows the interface language and the
    model, and a new wording must update the card, not start a second one with no history."""
    if origin in GENERATED:
        return sha256(entity_key + "\ngenerated")[:32]
    return sha256(entity_key + "\n" + q)[:32]


def upsert_card(g: Graph, entity_key: str, q: str, a: str, origin: str, priority: int = 0) -> None:
    cid = card_id(entity_key, q, origin)
    card = g.s.get(ReviewCard, cid)
    if card is None:
        g.s.add(ReviewCard(id=cid, entity_key=entity_key, q=q, a=a, origin=origin, priority=priority))
        g.s.flush()
    else:
        if origin in GENERATED:
            card.q, card.origin = q, origin
        card.a = a
        card.priority = max(card.priority, priority)


# ---- card identity across merges and rebuilds ------------------------------------------------

def _history(s, card: ReviewCard) -> int:
    from ..db.models import ReviewLog

    return s.execute(select(func.count()).select_from(ReviewLog).where(ReviewLog.card_id == card.id)).scalar_one()


def merge_cards(s, keep: ReviewCard, drop: ReviewCard) -> None:
    """One card for one question: keep the scheduling state of the one reviewed more, move the
    review history over, then delete the other."""
    from ..db.models import ReviewLog

    if keep.id == drop.id:
        return
    if _history(s, drop) > _history(s, keep):
        keep.state, keep.due, keep.introduced_at = drop.state, drop.due, drop.introduced_at
    keep.introduced_at = keep.introduced_at or drop.introduced_at
    keep.priority = max(keep.priority or 0, drop.priority or 0)
    keep.suspended = bool(keep.suspended and drop.suspended)
    s.execute(update(ReviewLog).where(ReviewLog.card_id == drop.id).values(card_id=keep.id))
    s.delete(drop)
    s.flush()


def rekey_card(s, card: ReviewCard, entity_key: str) -> ReviewCard:
    """Move a card to another entity (a merge) under the id it would have there."""
    from ..db.models import ReviewLog

    new_id = card_id(entity_key, card.q, card.origin)
    if new_id == card.id and card.entity_key == entity_key:
        return card
    target = s.get(ReviewCard, new_id)
    if target is not None and target is not card:
        merge_cards(s, target, card)
        return target
    clone = ReviewCard(id=new_id, entity_key=entity_key, q=card.q, a=card.a, origin=card.origin,
                       priority=card.priority, state=card.state, due=card.due, introduced_at=card.introduced_at,
                       suspended=card.suspended)
    s.execute(update(ReviewLog).where(ReviewLog.card_id == card.id).values(card_id=new_id))
    s.delete(card)
    s.flush()
    s.add(clone)
    s.flush()
    return clone


def reconcile_cards(g: Graph) -> dict[str, int]:
    """After a rebuild: cards follow merges, duplicates (older ids, language switches) collapse
    into one, cards whose entity disappeared (e.g. an auto-merge under different thresholds) join
    a live card with the same question or are suspended. Views of vanished keys are dropped and
    pending queue items whose entities are gone become obsolete."""
    from ..db.models import AccessLog, ReviewItem

    s = g.s
    live = set(s.execute(select(Entity.key)).scalars())
    out = {"rekeyed": 0, "merged": 0, "suspended": 0}
    by_q: dict[str, ReviewCard] = {}
    cards = list(s.execute(select(ReviewCard).order_by(ReviewCard.id)).scalars())
    for c in cards:
        key = g.resolve_key(c.entity_key)
        if key in live and (key != c.entity_key or c.id != card_id(key, c.q, c.origin)):
            c = rekey_card(s, c, key)
            out["rekeyed"] += 1
        if c.entity_key in live:
            by_q.setdefault(c.q, c)
    for c in list(s.execute(select(ReviewCard).order_by(ReviewCard.id)).scalars()):
        if c.entity_key in live:
            continue
        sibling = by_q.get(c.q)
        if sibling is not None and sibling.id != c.id:
            merge_cards(s, sibling, c)
            out["merged"] += 1
        elif not c.suspended:
            c.suspended = True
            out["suspended"] += 1
    for row in s.execute(select(AccessLog)).scalars().all():
        if g.resolve_key(row.entity_key) not in live:
            s.delete(row)
    for it in s.execute(select(ReviewItem).where(ReviewItem.status == "pending")).scalars():
        keys = [it.payload.get(k) for k in ("a", "b", "key", "topic") if isinstance(it.payload.get(k), str)]
        if keys and any(g.resolve_key(k) not in live for k in keys):
            it.status, it.resolved_at = "obsolete", utcnow()
    s.flush()
    return out


def _make_cards(g: Graph, doc: RxfDocument, work: Entity, assets: list[Entity], lookup) -> None:
    covered: set[str] = set()
    for rc in doc.review_cards:
        target = lookup(rc.about, ("method", "dataset", "idea", "claim", "topic")) if rc.about else None
        target = target or work
        covered.add(target.key)
        upsert_card(g, target.key, rc.q, rc.a, "rxf", priority=10 if target.attrs.get("origin") == "user" else 0)
    backend = get_backend()
    for e in assets:
        if e.key in covered or e.type == "claim" or (e.attrs or {}).get("no_cards"):
            continue
        qa = backend.make_card(_card_context(e, work))
        if qa:
            upsert_card(g, e.key, qa[0], qa[1], "local_llm")
            continue
        qa = template_card(e, work)
        if qa:
            upsert_card(g, e.key, qa[0], qa[1], "template", priority=10 if e.attrs.get("origin") == "user" else 0)


def _card_context(e: Entity, work: Entity) -> str:
    from .graph import entity_text

    return f"{entity_text(e)}\n(source: {work.canonical_name})"


def template_card(e: Entity, work: Entity) -> tuple[str, str] | None:
    a = e.attrs or {}
    title = work.canonical_name
    if e.type == "dataset" and e.external_id:
        ans = " / ".join(str(a[k]) for k in ("organism", "modality", "tissue") if a.get(k))
        return (_("card.dataset_q", accession=e.external_id), ans or e.canonical_name) if ans else None
    if e.type == "method" and a.get("io"):
        return _("card.method_q", io=a["io"], title=title), e.canonical_name
    if e.type == "idea":
        # the question needs a cue beyond the paper's title, or every idea of a paper gets the
        # same question with the answer in the card's header
        to = (a.get("transfer") or {}).get("to")
        if to:
            return _("card.idea_transfer_q", title=title, to=to), e.canonical_name
        links = a.get("link_names") or []
        if links:
            return _("card.linked_idea_q", items=" ↔ ".join(x[:80] for x in links[:3])), e.canonical_name
        if a.get("origin") == "user":
            return _("card.user_idea_q", title=title), e.canonical_name
        return None
    return None


# ---- OpenAlex (T0) -----------------------------------------------------------------------

def materialize_openalex(g: Graph, ex: Extraction) -> Entity:
    g.clock = ex.created_at
    try:
        return _materialize_openalex(g, ex)
    finally:
        g.clock = None


def _materialize_openalex(g: Graph, ex: Extraction) -> Entity:
    o = ex.output
    work = resolve_work(g, openalex_id=o["id"], doi=o.get("doi"), title=o.get("title") or o["id"],
                        year=o.get("year"))
    w = g.s.get(Work, work.id)
    w.year = o.get("year") or w.year
    g.update_attrs(work, abstract=o.get("abstract"), venue=o.get("venue"), authors=o.get("authors"),
                   oa_url=o.get("oa_url"), openalex_type=o.get("type"))
    g.reindex(work)
    return work


def link_citations(g: Graph, only_work_ids: set[int] | None = None) -> int:
    """cites edges among works in the library, from stored OpenAlex reference lists."""
    by_oa = {w.openalex_id: w.entity_id for w in g.s.execute(select(Work).where(Work.openalex_id.isnot(None))).scalars()}
    n = 0
    for ex in g.s.execute(select(Extraction).where(Extraction.kind == "openalex", Extraction.is_current)).scalars():
        src_id = by_oa.get(ex.output.get("id"))
        if src_id is None:
            continue
        for ref in ex.output.get("referenced_works", []):
            dst_id = by_oa.get(ref)
            if dst_id is None or (only_work_ids and src_id not in only_work_ids and dst_id not in only_work_ids):
                continue
            src, dst = g.by_id(src_id), g.by_id(dst_id)
            if g.upsert_edge(src, dst, "cites", extraction_id=ex.id) is not None:
                n += 1
    return n


# ---- retro-tagging results accepted by the local model ------------------------------

def materialize_retro(g: Graph, ex: Extraction) -> None:
    topic = g.by_key(ex.output["topic"])
    if topic is None:
        return
    g.clock = ex.created_at
    try:
        for a in ex.output.get("assignments", []):
            e = g.by_key(a["key"])
            if e is not None:
                g.upsert_edge(e, topic, a["relation"], confidence=a["confidence"], extraction_id=ex.id,
                              attrs={"via": "retro_tag"})
    finally:
        g.clock = None


# ---- topics ----------------------------------------------------------------------------------

def promote_topics(g: Graph) -> int:
    """candidate -> active once linked to >= N works (confirmed topics are already active)."""
    need = get_settings().thresholds.topic_promote_works
    n = 0
    for t in g.s.execute(select(Entity).where(Entity.type == "topic", Entity.status == "candidate")).scalars():
        works = g.s.execute(
            select(func.count(func.distinct(Edge.src))).join(Entity, Entity.id == Edge.src)
            .where(Edge.dst == t.id, Edge.type.in_(("about", "applicable_to")), Edge.status != "rejected",
                   Entity.type == "work")
        ).scalar_one()
        if works >= need:
            t.status = "active"
            n += 1
    return n


def materialize(g: Graph, ex: Extraction) -> Any:
    if ex.kind == "rxf":
        return materialize_rxf(g, ex)
    if ex.kind == "openalex":
        return materialize_openalex(g, ex)
    if ex.kind == "retro_tag":
        return materialize_retro(g, ex)
    raise ValueError(f"unknown extraction kind {ex.kind}")
