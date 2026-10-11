# SPDX-License-Identifier: Apache-2.0
"""Theme and accent are settings like the language: patched from the UI, reported to the
authenticated UI at boot, never to an anonymous caller."""

from fastapi.testclient import TestClient


def test_theme_and_accent_round_trip(settings, session):
    from rhizome.api.app import create_app

    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    h = c.get("/health").json()
    assert h["theme"] == "system" and h["accent"] == "green"
    r = c.patch("/settings", json={"theme": "dark", "accent": "violet"})
    assert r.status_code == 200 and r.json()["theme"] == "dark" and r.json()["accent"] == "violet"
    h = c.get("/health").json()
    assert h["theme"] == "dark" and h["accent"] == "violet"
    assert c.patch("/settings", json={"accent": "neon"}).status_code == 422
    assert c.patch("/settings", json={"theme": "sepia"}).status_code == 422
    anon = TestClient(app).get("/health").json()
    assert "theme" not in anon and "accent" not in anon
    assert (settings.data_dir / "settings.json").read_text().count('"violet"') == 1
