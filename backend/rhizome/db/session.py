# SPDX-License-Identifier: Apache-2.0
"""Engine/session management and migrations (Alembic, with automatic pre-upgrade backup)."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
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


def sqlite_ro_uri(path: Path) -> str:
    """file: URI for a read-only connection. Absolute Windows paths need file:///C:/..., and
    '%', '?', '#' and spaces in folder names must be percent-encoded."""
    from urllib.parse import quote

    p = path.as_posix()
    if not p.startswith("/"):
        p = "/" + p  # C:/x -> /C:/x
    return f"file://{quote(p, safe='/:')}?mode=ro"


def make_engine(url: str, read_only: bool = False) -> Engine:
    if url.startswith("sqlite"):
        if read_only:
            path = Path(url.split("sqlite:///", 1)[1]).resolve()
            uri = sqlite_ro_uri(path)
            engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True, check_same_thread=False))
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
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    # Migrations always run on a live connection (cfg.attributes["connection"]); the URL is only a
    # fallback for offline SQL generation. configparser treats '%' as interpolation and the rendered
    # URL percent-encodes Windows drive colons ("D%3A"), so escape it.
    cfg.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%"))
    return cfg


def current_revision(engine: Engine) -> str | None:
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision(engine: Engine) -> str | None:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config(engine)).get_current_head()


def backup_database(settings: Settings, tag: str = "backup") -> Path | None:
    """Consistent copy of the SQLite database (uses the online backup API), then prune old ones."""
    engine = get_engine(settings)
    if not is_sqlite(engine):
        return None
    src = Path(settings.db_url.split("sqlite:///", 1)[1])
    if not src.exists():
        return None
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    dst = settings.backups_dir / f"rhizome-{tag}-{datetime.now():%Y%m%d-%H%M%S}.db"
    copy_sqlite(src, dst)
    try:
        prune_backups(settings)
    except OSError:
        log.warning("could not prune old backups in %s", settings.backups_dir, exc_info=True)
    return dst


# How many backups of each kind are kept; manual backups are never deleted.
KEEP = {"pre-rebuild": 3, "pre-migration": 5, "other": 5}


def _backup_kind(p: Path) -> str:
    tag = p.stem[len("rhizome-"):].rsplit("-", 2)[0]  # rhizome-<tag>-YYYYmmdd-HHMMSS
    if tag in ("manual", "daily", "pre-rebuild"):
        return tag
    return "pre-migration" if tag.startswith("pre-") else "other"


def list_backups(settings: Settings) -> list[Path]:
    """Newest first."""
    d = settings.backups_dir
    files = [p for p in d.glob("rhizome-*.db") if p.is_file()] if d.is_dir() else []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def prune_backups(settings: Settings) -> list[Path]:
    """Keep the newest N of each kind (daily: settings.backup_keep_daily; manual: all) and remove
    temporary files left by an interrupted copy. Returns what was deleted."""
    keep = {**KEEP, "daily": settings.backup_keep_daily, "manual": None}
    seen: dict[str, int] = {}
    removed: list[Path] = []
    for p in list_backups(settings):
        kind = _backup_kind(p)
        seen[kind] = seen.get(kind, 0) + 1
        limit = keep.get(kind)
        if limit is not None and seen[kind] > limit:
            p.unlink()
            removed.append(p)
    cutoff = time.time() - 3600
    for tmp in settings.backups_dir.glob("*.tmp") if settings.backups_dir.is_dir() else []:
        if tmp.stat().st_mtime < cutoff:
            tmp.unlink()
            removed.append(tmp)
    return removed


def backup_status(settings: Settings) -> dict:
    files = list_backups(settings)
    return {"dir": str(settings.backups_dir), "count": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "last": datetime.fromtimestamp(files[0].stat().st_mtime).isoformat(timespec="seconds") if files else None,
            "last_daily": next((datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
                                for p in files if _backup_kind(p) == "daily"), None)}


DAILY_EVERY = 20 * 3600


def maybe_daily_backup(settings: Settings) -> Path | None:
    """Called at startup and periodically by the worker: one daily backup when the newest is older
    than 20 h (so a laptop that is only open in the evening still gets one per day)."""
    daily = [p for p in list_backups(settings) if _backup_kind(p) == "daily"]
    if daily and time.time() - daily[0].stat().st_mtime < DAILY_EVERY:
        return None
    return backup_database(settings, tag="daily")


def recent_backup(settings: Settings, tag: str, within: float) -> bool:
    return any(_backup_kind(p) == tag and time.time() - p.stat().st_mtime < within for p in list_backups(settings))


def copy_sqlite(src: Path, dst: Path) -> None:
    """Consistent copy of a live database. Connections are closed explicitly: sqlite3's context
    manager only commits, and Windows refuses to rename a file that is still open."""
    from contextlib import closing

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    with closing(sqlite3.connect(str(src))) as s, closing(sqlite3.connect(str(tmp))) as d:
        s.backup(d)
    os.replace(tmp, dst)


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
