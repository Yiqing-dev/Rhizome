# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "rxf-spec" / "examples"


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    from rhizome.config import Settings, set_settings
    from rhizome.db.session import dispose_all, init_db
    from rhizome.inference import reset_backend
    from rhizome.ml import reset_models
    from rhizome.pipeline.graph import VECTORS

    monkeypatch.setenv("RHIZOME_OFFLINE", "1")
    st = Settings(data_dir=tmp_path / "data", offline=True, language="en")
    set_settings(st)
    st.ensure_dirs()
    dispose_all()
    VECTORS.clear()
    reset_models()
    reset_backend()
    init_db(st)
    yield st
    dispose_all()


@pytest.fixture()
def session(settings):
    from rhizome.db.session import session_scope

    with session_scope() as s:
        yield s


def example(name: str) -> str:
    return (EXAMPLES / name).read_text(encoding="utf-8")


@pytest.fixture()
def library(session):
    """The three synthetic example papers ingested."""
    from rhizome.pipeline.ingest import ingest_text

    results = {}
    for f in ("deep-grn-atlas.yaml", "light-scenic-benchmark.yaml", "light-spatial-domains.yaml"):
        r = ingest_text(session, example(f), f)
        assert r.ok, r.report
        results[f] = r
    session.commit()
    return results
