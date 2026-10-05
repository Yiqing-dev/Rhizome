# SPDX-License-Identifier: Apache-2.0
"""rhizome-vocab.yaml: canonical topic names and zh/en aliases, for the chat Project knowledge.
With the vocabulary in the chat, exports use canonical names and most topics hit on exact alias."""

from __future__ import annotations

from datetime import date

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Edge, Entity, EntityAlias


def export_vocab(s: Session, include_candidates: bool = True) -> str:
    """Active topics under ``topics``; with ``include_candidates`` also the candidate topics that at
    least one paper is on, under ``candidate_topics`` with their status and paper count (new
    topics start as candidates, so a vocabulary of active ones alone stays empty for weeks)."""
    from sqlalchemy import func

    active = sorted(s.execute(select(Entity).where(Entity.type == "topic", Entity.status == "active")).scalars(),
                    key=lambda t: t.canonical_name.lower())
    works_of = dict(s.execute(
        select(Edge.dst, func.count(func.distinct(Edge.src))).join(Entity, Entity.id == Edge.src)
        .where(Edge.type.in_(("about", "applicable_to")), Edge.status != "rejected", Entity.type == "work")
        .group_by(Edge.dst)).all())
    candidates = []
    if include_candidates:
        candidates = sorted((t for t in s.execute(select(Entity).where(Entity.type == "topic", Entity.status == "candidate"))
                             .scalars() if works_of.get(t.id, 0) >= 1),
                            key=lambda t: (-works_of.get(t.id, 0), t.canonical_name.lower()))
    topics = active + candidates
    parents: dict[int, list[str]] = {}
    for src, dst_name in s.execute(select(Edge.src, Entity.canonical_name).join(Entity, Entity.id == Edge.dst)
                                   .where(Edge.type == "is_a", Edge.status != "rejected")).all():
        parents.setdefault(src, []).append(dst_name)
    out = []
    for t in topics:
        aliases = s.execute(select(EntityAlias).where(EntityAlias.entity_id == t.id)).scalars().all()
        entry = {"name": t.canonical_name}
        en = sorted({a.alias for a in aliases if a.lang == "en" and a.alias != t.canonical_name})
        zh = sorted({a.alias for a in aliases if a.lang == "zh"})
        if en:
            entry["aliases_en"] = en
        if zh:
            entry["aliases_zh"] = zh
        if (t.attrs or {}).get("definition"):
            entry["definition"] = t.attrs["definition"]
        if t.id in parents:
            entry["parents"] = sorted(parents[t.id])
        if t.status != "active":
            entry["status"] = t.status
            entry["papers"] = works_of.get(t.id, 0)
        out.append(entry)
    n_active = len(active)
    doc = {"topics": out[:n_active]}
    header = (f"# rhizome-vocab.yaml - generated {date.today().isoformat()}\n"
              "# Use `name` as the topic name in RXF exports; aliases are for matching only.\n")
    if include_candidates:
        doc["candidate_topics"] = out[n_active:]
        header += ("# candidate_topics are not confirmed yet: prefer a topic from `topics` when one fits,\n"
                   "# reuse a candidate's name when the paper is really about it, otherwise coin a new one.\n")
    return header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100)


VOCAB_KEY = "vocab_export"


def _digest(text: str) -> str:
    import hashlib

    body = "\n".join(ln for ln in text.splitlines() if not ln.startswith("#"))  # the date line is not a change
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def mark_exported(s: Session, text: str) -> None:
    from datetime import datetime, timezone

    from ..db.models import KV

    v = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "digest": _digest(text)}
    row = s.get(KV, VOCAB_KEY)
    if row is None:
        s.add(KV(k=VOCAB_KEY, v=v))
    else:
        row.v = v


def vocab_status(s: Session) -> dict:
    """{exported_at, stale}: stale when the vocabulary the Project holds differs from today's."""
    from ..db.models import KV

    row = s.get(KV, VOCAB_KEY)
    if row is None or not row.v:
        return {"exported_at": None, "stale": None}
    return {"exported_at": row.v.get("at"), "stale": row.v.get("digest") != _digest(export_vocab(s))}
