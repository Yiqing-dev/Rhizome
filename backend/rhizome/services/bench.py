# SPDX-License-Identifier: Apache-2.0
"""Retrieval benchmark (M0 pre-registered questions; also the CI retrieval regression set).

questions.yaml:
  threshold: {hit_at_5: 0.6}          # pre-registered gate, never edited after M0
  questions:
    - q: which dataset profiled Arabidopsis root nuclei?
      expect: [dataset:GSE999001]      # any of these keys counts as a hit
      types: [dataset]                 # optional filters, as in /search
"""

from __future__ import annotations

from typing import Any

import yaml
from sqlalchemy.orm import Session

from .search import DEFAULT_TYPES, Filters, search


class BenchSpecError(ValueError):
    pass


def resolve_expect(s: Session, entries: list[str]) -> set[str]:
    """Expected keys. ``type~name`` names an entity by alias (claims and ideas have hashed keys that
    nobody can write by hand); an entry that resolves to nothing is an error, never a silent miss."""
    from ..pipeline.graph import Graph

    g = Graph(s)
    out = set()
    for ent in entries:
        if "~" in ent:
            etype, _, name = ent.partition("~")
            hit = g.by_alias(etype, name)
            if hit is None:
                raise BenchSpecError(f"expect entry {ent!r}: no {etype} named {name!r} in this library")
            out.add(hit.key)
        else:
            out.add(ent)
    return out


def run(s: Session, spec_text: str, k: int = 10) -> dict[str, Any]:
    spec = yaml.safe_load(spec_text)
    rows = []
    for item in spec["questions"]:
        f = Filters(types=tuple(item.get("types") or DEFAULT_TYPES), organism=item.get("organism"),
                    modality=item.get("modality"))
        keys = [h["key"] for h in search(s, item["q"], f, limit=k)]
        expect = resolve_expect(s, item["expect"])
        rank = next((i + 1 for i, key in enumerate(keys) if key in expect), None)
        rows.append({"q": item["q"], "rank": rank, "top": keys[:3]})
    n = len(rows) or 1
    metrics = {
        "hit_at_1": sum(1 for r in rows if r["rank"] == 1) / n,
        "hit_at_5": sum(1 for r in rows if r["rank"] and r["rank"] <= 5) / n,
        "mrr": sum(1 / r["rank"] for r in rows if r["rank"]) / n,
    }
    gate = spec.get("threshold") or {}
    passed = all(metrics.get(m, 0) >= v for m, v in gate.items())
    return {"metrics": metrics, "threshold": gate, "passed": passed, "questions": rows,
            "missed": [r["q"] for r in rows if r["rank"] is None]}
