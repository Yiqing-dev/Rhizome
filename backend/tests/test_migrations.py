# SPDX-License-Identifier: Apache-2.0
"""Schema upgrades: one transaction, serialised across processes, backed up once, refused (without
touching anything) when the library is newer than this program, and no drift between models and
migrations."""

import multiprocessing as mp
import shutil
import sqlite3
import textwrap

import pytest

from conftest import example

FAIL_0002 = '''
revision = "9002"
down_revision = "{head}"
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table("half_done", sa.Column("x", sa.Integer))
    op.execute("insert into half_done values (1)")
    raise RuntimeError("power cut")

def downgrade():
    pass
'''

# rebuilds the entity table (batch mode copies it): with foreign keys on, dropping the old table
# would cascade-delete every alias, edge and work row
OK_0002 = '''
revision = "9002"
down_revision = "{head}"
from alembic import op
import sqlalchemy as sa

def upgrade():
    with op.batch_alter_table("entity", recreate="always") as b:
        b.add_column(sa.Column("note", sa.String(10), nullable=True))

def downgrade():
    pass
'''


def _fake_migrations(tmp_path, monkeypatch, body):
    from rhizome.db import session as dbs

    d = tmp_path / "migrations"
    shutil.copytree(dbs.MIGRATIONS_DIR, d, ignore=shutil.ignore_patterns("__pycache__"))
    head = dbs.head_revision(dbs.get_engine())
    (d / "versions" / "9002_fake.py").write_text(textwrap.dedent(body).replace("{head}", head), "utf-8")
    monkeypatch.setattr(dbs, "MIGRATIONS_DIR", d)
    return d


def _rev(db):
    with sqlite3.connect(db) as c:
        return c.execute("select version_num from alembic_version").fetchone()[0]


def _tables(db):
    with sqlite3.connect(db) as c:
        return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}


def _library(settings):
    from rhizome.db.session import dispose_all, session_scope
    from rhizome.pipeline.ingest import ingest_text

    with session_scope() as s:
        assert ingest_text(s, example("deep-grn-atlas.yaml"), "a.yaml").ok
    dispose_all()
    return settings.data_dir / "rhizome.db"


def _count(db, table):
    with sqlite3.connect(db) as c:
        return c.execute(f"select count(*) from {table}").fetchone()[0]


def test_failed_migration_leaves_schema_and_data_untouched(settings, tmp_path, monkeypatch):
    from rhizome.db.session import init_db

    db = _library(settings)
    edges = _count(db, "edge")
    _fake_migrations(tmp_path, monkeypatch, FAIL_0002)
    with pytest.raises(RuntimeError, match="power cut"):
        init_db(settings)
    assert _rev(db) != "9002" and "half_done" not in _tables(db)  # DDL rolled back too
    assert _count(db, "edge") == edges


def test_table_rebuild_migration_keeps_child_rows(settings, tmp_path, monkeypatch):
    from rhizome.db.session import init_db, list_backups

    db = _library(settings)
    counts = {t: _count(db, t) for t in ("entity", "entity_alias", "edge", "work")}
    _fake_migrations(tmp_path, monkeypatch, OK_0002)
    init_db(settings)
    assert _rev(db) == "9002"
    assert {t: _count(db, t) for t in counts} == counts  # no cascade delete through the copy
    assert [p.name for p in list_backups(settings)][0].startswith("rhizome-pre-9002-")
    init_db(settings)  # already at head: nothing more
    assert len(list_backups(settings)) == 1


def _start(args):
    data_dir, mig_dir = args
    from pathlib import Path

    from rhizome.config import Settings, set_settings
    from rhizome.db import session as dbs

    dbs.MIGRATIONS_DIR = Path(mig_dir)
    st = Settings(data_dir=Path(data_dir), offline=True)
    set_settings(st)
    dbs.init_db(st)
    return "ok"


def test_concurrent_starts_migrate_once(settings, tmp_path, monkeypatch):
    from rhizome.db.session import list_backups

    db = _library(settings)
    mig = _fake_migrations(tmp_path, monkeypatch, OK_0002)
    with mp.get_context("spawn").Pool(3) as pool:
        assert pool.map(_start, [(str(settings.data_dir), str(mig))] * 3) == ["ok"] * 3
    assert _rev(db) == "9002" and len(list_backups(settings)) == 1


def test_fresh_libraries_created_concurrently(tmp_path):
    from rhizome.db import session as dbs

    with mp.get_context("spawn").Pool(3) as pool:
        assert pool.map(_start, [(str(tmp_path / "new"), str(dbs.MIGRATIONS_DIR))] * 3) == ["ok"] * 3


def test_library_from_a_newer_version_is_refused_untouched(settings):
    from rhizome.db.session import SchemaTooNew, dispose_all, init_db, list_backups

    db = _library(settings)
    with sqlite3.connect(db) as c:
        c.execute("update alembic_version set version_num = '9999'")
    dispose_all()
    with pytest.raises(SchemaTooNew, match="9999"):
        init_db(settings)
    assert list_backups(settings) == [] and _rev(db) == "9999"


def test_models_and_migrations_do_not_drift(settings):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from rhizome.db.models import Base
    from rhizome.db.session import get_engine
    from rhizome.db.session import include_object

    with get_engine(settings).connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"include_object": include_object})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], diff
