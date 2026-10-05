# SPDX-License-Identifier: Apache-2.0
"""A model named in settings.json that this process cannot load (the installed app has no torch)."""

import json

import pytest
from fastapi.testclient import TestClient

from conftest import example


@pytest.fixture()
def no_hf(monkeypatch):
    from rhizome.ml import registry

    monkeypatch.setattr(registry, "_importable", lambda m: False)
    registry.reset_models()


def test_unloadable_model_is_reported_not_mixed(settings, no_hf):
    from rhizome.config import update_settings
    from rhizome.ml import ModelUnavailable, get_embedder, model_problems

    (settings.data_dir / "settings.json").write_text(json.dumps({"embedder": "bge-m3"}), "utf-8")
    from rhizome.config import load_settings, set_settings

    set_settings(load_settings(settings.data_dir))
    assert model_problems() == [{"kind": "embedder", "model": "bge-m3", "missing": "sentence_transformers"}]
    with pytest.raises(ModelUnavailable):
        get_embedder()
    update_settings({"embedder": None})
    assert model_problems() == []


def test_api_rejects_and_reports_unloadable_models(settings, no_hf):
    from rhizome.api.app import create_app
    from rhizome.config import load_settings, set_settings

    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    assert c.patch("/settings", json={"embedder": "bge-m3"}).status_code == 422
    (settings.data_dir / "settings.json").write_text(json.dumps({"embedder": "bge-m3"}), "utf-8")
    set_settings(load_settings(settings.data_dir))
    assert c.get("/system").json()["model_problems"][0]["model"] == "bge-m3"
    r = c.get("/search", params={"q": "RootNet"})
    assert r.status_code == 503 and r.json()["model_unavailable"]


def test_inbox_file_stays_put_when_the_model_is_missing(settings, no_hf):
    from rhizome.config import load_settings, set_settings
    from rhizome.pipeline.ingest import ingest_file

    (settings.data_dir / "settings.json").write_text(json.dumps({"embedder": "bge-m3"}), "utf-8")
    set_settings(load_settings(settings.data_dir))
    f = settings.inbox / "paper.yaml"
    f.write_text(example("light-spatial-domains.yaml"), "utf-8")
    assert not ingest_file(f).ok
    assert f.exists() and not (settings.inbox / "error" / "paper.yaml").exists()
