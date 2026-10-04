# SPDX-License-Identifier: Apache-2.0
"""Opt-in fixes for known export drift.

Never applied silently: ingest only repairs when the user asks (``rhz ingest --repair``, the
"repair and import" button). The original bytes stay in L0 unchanged; the repaired document is the
L1 output and ``meta.repairs`` lists what was changed, so a drifting export prompt stays visible.
"""

from __future__ import annotations

import copy
from typing import Any

# code -> what it fixes (codes are stable: they are stored in L1 meta and used as i18n keys)
REPAIRS = ("transfer_flattened", "topic_new_flag")

_TRANSFER_KEYS = ("type", "to", "barrier")


def repair(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return a repaired deep copy and the codes of the repairs that changed something."""
    out = copy.deepcopy(data)
    applied: list[str] = []
    if _fix_transfer(out):
        applied.append("transfer_flattened")
    if _fix_topic_new(out):
        applied.append("topic_new_flag")
    return out, applied


def _fix_transfer(d: dict[str, Any]) -> bool:
    """ideas[].transfer written flat: `transfer: cross-species` with `to` / `barrier` beside it, or
    `transfer_type` / `transfer_to` / `transfer_barrier`, or `type` / `to` / `barrier` on the idea."""
    assets = d.get("assets")
    ideas = assets.get("ideas") if isinstance(assets, dict) else None
    changed = False
    for idea in ideas if isinstance(ideas, list) else []:
        if not isinstance(idea, dict):
            continue
        t = idea.get("transfer")
        nested: dict[str, Any] = dict(t) if isinstance(t, dict) else {}
        moved = False
        if isinstance(t, str):
            nested["type"] = t
            moved = True
        for k in _TRANSFER_KEYS:
            for flat in (f"transfer_{k}", k):
                if flat in idea and flat != "transfer":
                    nested.setdefault(k, idea.pop(flat))
                    moved = True
        if moved and nested:
            idea["transfer"] = nested
            changed = True
    return changed


def _fix_topic_new(d: dict[str, Any]) -> bool:
    """topics[].new: whether a topic is new is decided on import by alias matching."""
    changed = False
    for t in d.get("topics") or []:
        if isinstance(t, dict) and "new" in t:
            t.pop("new")
            changed = True
    return changed
