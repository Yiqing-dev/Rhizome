# SPDX-License-Identifier: Apache-2.0
"""Remember generative judgements in the library, so a rebuild never asks the model again.

A rebuild replays every export, and materialising an export asks the inference backend for
topic-breadth verdicts and flashcards. With a paid API behind the backend that would bill the same
questions on every rebuild; with the local model it would cost minutes. Answers are stored in the
KV table under a hash of (backend, model, question); the queue backend answers nothing and stores
nothing.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import KV
from ..text import sha256
from . import get_backend


def _key(backend: Any, method: str, args: tuple) -> str:
    who = f"{backend.name}:{getattr(backend, 'model', '')}"
    return "inf:" + sha256(json.dumps([who, method, list(args)], ensure_ascii=False, default=str))[:48]


def judged(s: Session, method: str, *args: Any, backend: Any = None) -> Any:
    """``backend.<method>(*args)`` (default: the configured backend), answered from the library
    when the same backend and model were asked the same question before."""
    backend = backend if backend is not None else get_backend()
    if backend.name == "queue":
        return None
    k = _key(backend, method, args)
    row = s.get(KV, k)
    if row is not None and row.v is not None:
        return tuple(row.v)
    out = getattr(backend, method)(*args)
    if out is not None and not s.info.get("read_only"):
        s.add(KV(k=k, v=list(out)))
        s.flush()
    return out
