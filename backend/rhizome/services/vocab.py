# SPDX-License-Identifier: Apache-2.0
"""rhizome-vocab.yaml: canonical topic names and zh/en aliases, for the chat Project knowledge.
With the vocabulary in the chat, exports use canonical names and most topics hit on exact alias."""

from __future__ import annotations

from datetime import date

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Edge, Entity, EntityAlias


def export_vocab(s: Session, include_candidates: bool = False) -> str:
    q = select(Entity).where(Entity.type == "topic")
    if not include_candidates:
        q = q.where(Entity.status == "active")
    topics = sorted(s.execute(q).scalars(), key=lambda t: t.canonical_name.lower())
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
        out.append(entry)
    header = (f"# rhizome-vocab.yaml - generated {date.today().isoformat()}\n"
              "# Use `name` as the topic name in RXF exports; aliases are for matching only.\n")
    return header + yaml.safe_dump({"topics": out}, allow_unicode=True, sort_keys=False, width=100)
