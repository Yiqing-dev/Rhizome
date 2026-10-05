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


ROLLOVER_HOUR = 4  # a review at 1 am still belongs to the evening's day


def _day_start(now: datetime) -> datetime:
    """Start of the current review day in *local* time (naive UTC in, naive UTC out)."""
    local = now.replace(tzinfo=timezone.utc).astimezone()
    start = local.replace(hour=ROLLOVER_HOUR, minute=0, second=0, microsecond=0)
    if local < start:
        start -= timedelta(days=1)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


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
    alive = _alive_cards()
    due = list(s.execute(select(ReviewCard).where(alive, ReviewCard.introduced_at.isnot(None), ReviewCard.due <= now)
                         .order_by(ReviewCard.priority.desc(), ReviewCard.due).limit(min(limit, room))).scalars())
    new_room = max(0, min(st.review_daily_new - introduced, room - len(due), limit - len(due)))
    if new_room:
        due += list(s.execute(select(ReviewCard).where(alive, ReviewCard.introduced_at.is_(None))
                              .order_by(ReviewCard.priority.desc(), ReviewCard.created_at.desc().nulls_last(),
                                        ReviewCard.id).limit(new_room)).scalars())
    names = dict(s.execute(select(Entity.key, Entity.canonical_name)
                           .where(Entity.key.in_([c.entity_key for c in due]))).all())
    return [{"id": c.id, "q": c.q, "a": c.a, "entity_key": c.entity_key, "entity_name": names.get(c.entity_key),
             "origin": c.origin, "new": c.introduced_at is None, "priority": c.priority} for c in due]


def _alive_cards():
    """Not suspended, and about an entity that still stands: not rejected, and (for an asset that
    came from papers) with at least one source edge that is not rejected. Decided at query time,
    so a rejection or a retraction takes the cards out at once."""
    from sqlalchemy import exists

    from ..db.models import Edge
    from .search import SOURCE_EDGE_TYPES

    ent = select(Entity).where(Entity.key == ReviewCard.entity_key).correlate(ReviewCard)
    rejected = exists(ent.where(Entity.status == "rejected"))
    any_source = exists(select(Edge.id).where(Edge.dst == Entity.id, Edge.type.in_(SOURCE_EDGE_TYPES)))
    live_source = exists(select(Edge.id).where(Edge.dst == Entity.id, Edge.type.in_(SOURCE_EDGE_TYPES),
                                              Edge.status != "rejected"))
    orphaned = exists(ent.where(Entity.type != "work", any_source, ~live_source))
    return ~ReviewCard.suspended & ~rejected & ~orphaned


def due_count(s: Session) -> int:
    """What a review session would serve now (the daily caps applied), for the home page."""
    return len(due_cards(s, limit=get_settings().review_daily_max))


def backlog_count(s: Session) -> int:
    now = utcnow()
    return s.execute(select(func.count()).select_from(ReviewCard).where(
        _alive_cards(), or_(ReviewCard.introduced_at.is_(None), ReviewCard.due <= now))).scalar_one()


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


def suspend(s: Session, card_id: str, suspended: bool = True, entity: bool = False) -> dict[str, Any]:
    """Take a card out of rotation (or back in). ``entity``: also flag the asset so no card is ever
    generated for it again, and suspend its other cards."""
    from sqlalchemy import update

    c = s.get(ReviewCard, card_id)
    if c is None:
        raise LookupError(card_id)
    c.suspended = suspended
    n = 1
    if entity:
        e = s.execute(select(Entity).where(Entity.key == c.entity_key)).scalar_one_or_none()
        if e is not None:
            e.attrs = {**(e.attrs or {}), "no_cards": suspended}
        n = s.execute(update(ReviewCard).where(ReviewCard.entity_key == c.entity_key)
                      .values(suspended=suspended)).rowcount or 1
    return {"id": c.id, "suspended": c.suspended, "cards": n}
