# SPDX-License-Identifier: Apache-2.0
"""Engine/session management and migrations (Alembic, with automatic pre-upgrade backup)."""

from __future__ import annotations

import logging
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings

log = logging.getLogger(__name__)

_engines: dict[str, Engine] = {}
_makers: dict[str, sessionmaker[Session]] = {}

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def is_sqlite(engine: Engine) -> bool:
    return engine.dialect.name == "sqlite"


def make_engine(url: str, read_only: bool = False) -> Engine:
    if url.startswith("sqlite"):
        if read_only:
            path = url.split("sqlite:///", 1)[1]
            engine = create_engine(
                f"sqlite:///file:{Path(path).as_posix()}?mode=ro&uri=true",
                connect_args={"check_same_thread": False},
            )
        else:
            engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if not read_only:
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def get_engine(settings: Settings | None = None, read_only: bool = False) -> Engine:
    settings = settings or get_settings()
    url = settings.db_url
    cache_key = f"{url}|ro={read_only}"
    if cache_key not in _engines:
        if url.startswith("sqlite") and not read_only:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
        _engines[cache_key] = make_engine(url, read_only=read_only)
        _makers[cache_key] = sessionmaker(bind=_engines[cache_key], expire_on_commit=False)
    return _engines[cache_key]


def dispose_all() -> None:
    for e in _engines.values():
        e.dispose()
    _engines.clear()
    _makers.clear()


@contextmanager
def session_scope(settings: Settings | None = None, read_only: bool = False) -> Iterator[Session]:
    settings = settings or get_settings()
    get_engine(settings, read_only)
    maker = _makers[f"{settings.db_url}|ro={read_only}"]
    s = maker()
    s.info["read_only"] = read_only
    try:
        yield s
        if not read_only:
            s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ---- migrations ---------------------------------------------------------------

def _alembic_config(engine: Engine):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))
    return cfg


def current_revision(engine: Engine) -> str | None:
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision(engine: Engine) -> str | None:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config(engine)).get_current_head()


def backup_database(settings: Settings, tag: str = "backup") -> Path | None:
    """Consistent copy of the SQLite database (uses the online backup API)."""
    engine = get_engine(settings)
    if not is_sqlite(engine):
        return None
    src = Path(settings.db_url.split("sqlite:///", 1)[1])
    if not src.exists():
        return None
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    dst = settings.backups_dir / f"rhizome-{tag}-{datetime.now():%Y%m%d-%H%M%S}.db"
    copy_sqlite(src, dst)
    return dst


def copy_sqlite(src: Path, dst: Path) -> None:
    import sqlite3

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    with sqlite3.connect(str(src)) as s, sqlite3.connect(str(tmp)) as d:
        s.backup(d)
    shutil.move(str(tmp), str(dst))


def init_db(settings: Settings | None = None) -> Engine:
    """Create or upgrade the schema to head. Backs up first when an upgrade is needed."""
    from alembic import command

    settings = settings or get_settings()
    engine = get_engine(settings)
    cur, head = current_revision(engine), head_revision(engine)
    if cur != head:
        if cur is not None:
            path = backup_database(settings, tag=f"pre-{head}")
            log.info("backed up database to %s before migrating %s -> %s", path, cur, head)
        with engine.begin() as conn:
            cfg = _alembic_config(engine)
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
    return engine


def has_fts(session: Session) -> bool:
    if session.get_bind().dialect.name != "sqlite":
        return False
    row = session.execute(
        text("select 1 from sqlite_master where type='table' and name='entity_fts'")
    ).first()
    return row is not None
