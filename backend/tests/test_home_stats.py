# SPDX-License-Identifier: Apache-2.0
"""The Home page's "recently captured" list and 7-day deltas come from /stats."""

from datetime import timedelta

from fastapi.testclient import TestClient


def test_stats_lists_recent_works_with_their_asset_counts(settings, library, session):
    from rhizome.api.app import create_app
    from rhizome.db.models import Entity, utcnow

    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    st = c.get("/stats").json()
    assert st["week_days"] == 7
    assert st["week"]["work"] == 3  # every example paper was captured just now
    recent = st["recent"]
    assert [r["name"] for r in recent] == [r["name"] for r in sorted(recent, key=lambda r: -r["id"])]
    assert len(recent) == 3
    deep = next(r for r in recent if r["depth"] == "deep")
    assert deep["via"] == "rxf" and deep["created_at"]
    assert deep["counts"].get("claim", 0) > 0 and deep["counts"].get("method", 0) > 0
    assert set(deep["counts"]) <= {"dataset", "method", "idea", "claim"}

    # a paper captured long ago drops out of the week's count but stays in the list
    old = session.query(Entity).filter(Entity.type == "work").order_by(Entity.id).first()
    old.created_at = utcnow() - timedelta(days=30)
    session.commit()
    st = c.get("/stats").json()
    assert st["week"]["work"] == 2
    assert len(st["recent"]) == 3
