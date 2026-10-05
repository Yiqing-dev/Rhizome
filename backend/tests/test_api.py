# SPDX-License-Identifier: Apache-2.0
import pytest
from fastapi.testclient import TestClient

from conftest import example


@pytest.fixture()
def api(settings):
    from rhizome.api.app import create_app

    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    return c


def test_rxf_instructions_endpoint(api):
    zh = api.get("/rxf/instructions", params={"lang": "zh_CN"})
    assert zh.status_code == 200 and "rxf_version: 1" in zh.text and "规则" in zh.text
    assert "Rules" in api.get("/rxf/instructions", params={"lang": "en"}).text


def test_auth_required(settings):
    from rhizome.api.app import create_app

    c = TestClient(create_app(settings, start_worker=False))
    assert c.get("/health").status_code == 200
    assert c.get("/stats").status_code == 401
    assert c.get("/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_ingest_search_card_flow(api):
    r = api.post("/ingest", json={"text": example("deep-grn-atlas.yaml"), "filename": "a.yaml"})
    assert r.status_code == 200, r.text
    wid = r.json()["work_id"]
    bad = api.post("/ingest", json={"text": example("invalid/missing-evidence.yaml")})
    assert bad.status_code == 422 and "claims.0" in bad.json()["detail"]["report"]
    multipart = api.post("/ingest", files={"file": ("b.yaml", example("light-spatial-domains.yaml").encode()),
                                          "pdf": ("b.pdf", b"%PDF synthetic")})
    assert multipart.status_code == 200
    hits = api.get("/search", params={"q": "RootNet", "types": "method"}).json()["results"]
    assert hits[0]["name"] == "RootNet"
    card = api.get(f"/entity/{wid}").json()
    assert card["type"] == "work" and card["exports"]
    assert api.get("/entity/by-key", params={"key": "dataset:GSE999001"}).json()["external_id"] == "GSE999001"
    assert api.get(f"/entity/{wid}/neighbors", params={"hops": 2}).json()["nodes"]
    assert api.get("/topic-map", params={"include_candidates": True}).json()["nodes"]
    assert api.post("/recall", json={"text": "accessibility priors"}).status_code == 200
    assert "GEO" in api.get("/data/GSE999001").json()["download"]
    assert api.get("/entity/999999").status_code == 404


def test_topic_review_cards_jobs(api):
    from rhizome import jobs

    api.post("/ingest", json={"text": example("deep-grn-atlas.yaml")})
    r = api.post("/topic", json={"name": "chromatin accessibility", "definition": "ATAC-seq and open chromatin"})
    assert r.status_code == 200
    jobs.run_all()
    assert api.get(f"/jobs/{r.json()['job_id']}").json()["status"] == "done"
    q = api.get("/review").json()
    assert "items" in q
    cards = api.get("/cards/due").json()["cards"]
    assert api.post(f"/cards/{cards[0]['id']}/grade", json={"rating": 4}).status_code == 200
    assert api.post("/decision", json={"op": "nope", "payload": {}}).status_code == 422
    d = api.post("/decision", json={"op": "add_alias", "payload": {"key": "topic:grn inference",
                                                                   "alias": "基因调控网络"}}).json()
    rv = api.delete(f"/decision/{d['id']}").json()
    assert rv["rebuild_job"]
    assert "GRN inference" in api.get("/vocab", params={"include_candidates": True}).text


def test_settings_patch_persists(api, settings):
    r = api.patch("/settings", json={"language": "zh_CN", "thresholds": {"merge_auto": 0.95}})
    assert r.json()["ui_language"] == "zh_CN"
    assert (settings.data_dir / "settings.json").exists()
    assert api.get("/settings").json()["thresholds"]["merge_auto"] == 0.95


def test_snapshot_is_read_only(settings, tmp_path, library, session):
    from rhizome.api.app import create_app
    from rhizome.config import Settings
    from rhizome.services.snapshot import make_snapshot

    session.commit()
    snap = make_snapshot(settings, tmp_path / "snap" / "rhizome.db")
    ro = Settings(data_dir=snap.parent, database_url=f"sqlite:///{snap.as_posix()}", offline=True)
    app = create_app(ro, read_only=True, start_worker=False, token="t")
    c = TestClient(app, headers={"Authorization": "Bearer t"})
    assert c.get("/search", params={"q": "RootNet"}).json()["results"]
    assert c.post("/ingest", json={"text": "x"}).status_code == 403
    assert c.get("/entity/1").status_code == 200


def test_browse_by_type_without_query(api):
    api.post("/ingest", json={"text": example("deep-grn-atlas.yaml")})
    r = api.get("/search", params={"types": "dataset"}).json()["results"]
    assert [h["name"] for h in r] == ["GSE999001"] and r[0]["sources"]
    assert api.get("/search").json()["results"]  # no filters at all: newest entities of the default types
