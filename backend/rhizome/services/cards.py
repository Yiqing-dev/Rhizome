# SPDX-License-Identifier: Apache-2.0
"""Spaced repetition with FSRS. Cards are per asset; the user's own ideas come first.
Daily caps on new and total cards keep the load bounded as the library grows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fsrs import Card, Rating, Scheduler
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Entity, ReviewCard, ReviewLog, utcnow
from .recall import touch

_scheduler = Scheduler()


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _counts_today(s: Session, now: datetime) -> tuple[int, int]:
    start = _day_start(now)
    reviewed = s.execute(select(func.count()).select_from(ReviewLog).where(ReviewLog.reviewed_at >= start)).scalar_one()
    introduced = s.execute(select(func.count()).select_from(ReviewCard)
                           .where(ReviewCard.introduced_at >= start)).scalar_one()
    return reviewed, introduced


def due_cards(s: Session, limit: int = 20, now: datetime | None = None) -> list[dict[str, Any]]:
    st = get_settings()
    now = now or utcnow()
    reviewed, introduced = _counts_today(s, now)
    room = max(0, st.review_daily_max - reviewed)
    if room == 0:
        return []
    due = list(s.execute(select(ReviewCard).where(ReviewCard.introduced_at.isnot(None), ~ReviewCard.suspended,
                                                  ReviewCard.due <= now)
                         .order_by(ReviewCard.priority.desc(), ReviewCard.due).limit(min(limit, room))).scalars())
    new_room = max(0, min(st.review_daily_new - introduced, room - len(due), limit - len(due)))
    if new_room:
        due += list(s.execute(select(ReviewCard).where(ReviewCard.introduced_at.is_(None), ~ReviewCard.suspended)
                              .order_by(ReviewCard.priority.desc(), ReviewCard.id).limit(new_room)).scalars())
    names = dict(s.execute(select(Entity.key, Entity.canonical_name)
                           .where(Entity.key.in_([c.entity_key for c in due]))).all())
    return [{"id": c.id, "q": c.q, "a": c.a, "entity_key": c.entity_key, "entity_name": names.get(c.entity_key),
             "origin": c.origin, "new": c.introduced_at is None, "priority": c.priority} for c in due]


def due_count(s: Session) -> int:
    now = utcnow()
    return s.execute(select(func.count()).select_from(ReviewCard).where(
        ~ReviewCard.suspended, or_(ReviewCard.introduced_at.is_(None), ReviewCard.due <= now))).scalar_one()


def grade(s: Session, card_id: str, rating: int, now: datetime | None = None) -> dict[str, Any]:
    if rating not in (1, 2, 3, 4):
        raise ValueError("rating must be 1 (again) .. 4 (easy)")
    c = s.get(ReviewCard, card_id)
    if c is None:
        raise LookupError(card_id)
    now = now or utcnow()
    fc = Card.from_dict(c.state) if c.state else Card(due=now.replace(tzinfo=timezone.utc))
    fc, _log = _scheduler.review_card(fc, Rating(rating), review_datetime=now.replace(tzinfo=timezone.utc))
    c.state = fc.to_dict()
    c.due = fc.due.astimezone(timezone.utc).replace(tzinfo=None)
    if c.introduced_at is None:
        c.introduced_at = now
    s.add(ReviewLog(card_id=c.id, rating=rating, reviewed_at=now))
    touch(s, c.entity_key)
    return {"id": c.id, "due": c.due.isoformat(), "interval_days": round((c.due - now) / timedelta(days=1), 2)}


def suspend(s: Session, card_id: str, suspended: bool = True) -> None:
    c = s.get(ReviewCard, card_id)
    if c is None:
        raise LookupError(card_id)
    c.suspended = suspended
