# SPDX-License-Identifier: Apache-2.0
"""Engine/session management and migrations (Alembic, with automatic pre-upgrade backup)."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
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
_registry_lock = threading.Lock()  # engines are created from request threads and the preload thread

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def is_sqlite(engine: Engine) -> bool:
    return engine.dialect.name == "sqlite"


def sqlite_ro_uri(path: Path) -> str:
    """file: URI for a read-only connection. Absolute Windows paths need file:///C:/..., and
    '%', '?', '#' and spaces in folder names must be percent-encoded. A rollback-journal file
    (snapshots) is opened ``immutable``: no -wal/-shm files are created next to it, so it works
    from a read-only folder and on NFS/Lustre. The live WAL library never is: immutable would
    hide other processes' writes."""
    from urllib.parse import quote

    p = path.as_posix()
    if not p.startswith("/"):
        p = "/" + p  # C:/x -> /C:/x
    return f"file://{quote(p, safe='/:')}?mode=ro" + ("&immutable=1" if not is_wal_file(path) else "")


def is_wal_file(path: Path) -> bool:
    """Header bytes 18/19 are the read/write format: 2 = WAL, 1 = rollback journal."""
    try:
        with open(path, "rb") as f:
            head = f.read(20)
    except OSError:
        return True
    return len(head) < 20 or head[18] == 2 or head[19] == 2


def _enable_wal(cur, wait: float = 30.0) -> None:
    """PRAGMA journal_mode=WAL fails at once with 'database is locked' (the busy timeout does not
    apply) while another connection writes, which happens when two processes open a brand-new
    library together. Retry; once any process switched the file, WAL is persistent."""
    deadline = time.monotonic() + wait
    while True:
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) and "busy" not in str(e):
                raise
            if cur.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
                return
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def make_engine(url: str, read_only: bool = False) -> Engine:
    if url.startswith("sqlite"):
        if read_only:
            path = Path(url.split("sqlite:///", 1)[1]).resolve()
            uri = sqlite_ro_uri(path)
            engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True, check_same_thread=False,
                                                                                   timeout=30))
        else:
            engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if not read_only:
                _enable_wal(cur)
                # FULL: a commit survives a power cut (NORMAL in WAL mode may lose the last
                # transactions, i.e. a paper whose export already moved to inbox/done). Commits
                # are few and the extra fsync is cheap next to embedding.
                cur.execute("PRAGMA synchronous=FULL")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def get_engine(settings: Settings | None = None, read_only: bool = False) -> Engine:
    settings = settings or get_settings()
    url = settings.db_url
    cache_key = f"{url}|ro={read_only}"
    with _registry_lock:
        if cache_key not in _engines:
            if url.startswith("sqlite") and not read_only:
                settings.data_dir.mkdir(parents=True, exist_ok=True)
            _engines[cache_key] = make_engine(url, read_only=read_only)
            _makers[cache_key] = sessionmaker(bind=_engines[cache_key], expire_on_commit=False)
        return _engines[cache_key]


def dispose_all() -> None:
    with _registry_lock:
        engines = list(_engines.values())
        _engines.clear()
        _makers.clear()
    for e in engines:
        e.dispose()


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


def backup_revision(path: Path) -> str | None:
    try:
        with closing_ro(path) as c:
            row = c.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def closing_ro(path: Path):
    from contextlib import closing

    return closing(sqlite3.connect(sqlite_ro_uri(Path(path).resolve()), uri=True))


class RestoreError(RuntimeError):
    pass


def restore_database(settings: Settings, backup: Path) -> Path | None:
    """Copy a backup over the live library *through SQLite* (backup API), never by swapping files:
    a stale -wal file next to a swapped-in database corrupts it. Refuses while any other process
    has the library open, checks the backup first, and keeps a pre-restore copy of the current
    state. Returns that copy."""
    from ..i18n import _

    backup = Path(backup)
    if not backup.is_file():
        raise RestoreError(_("db.restore_missing", path=str(backup)))
    try:
        with closing_ro(backup) as c:
            ok = c.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as e:
        raise RestoreError(_("db.restore_bad", path=str(backup), error=str(e))) from e
    if ok != "ok":
        raise RestoreError(_("db.restore_bad", path=str(backup), error=ok))
    rev = backup_revision(backup)
    engine = get_engine(settings)
    if rev is not None and not _known_revision(engine, rev):
        raise RestoreError(_("db.restore_newer", revision=rev))
    dispose_all()  # our own pooled connections would count as "in use"
    live = Path(settings.db_url.split("sqlite:///", 1)[1])
    pre = backup_database(settings, tag="pre-restore") if live.exists() else None
    dispose_all()
    from contextlib import closing

    with closing(sqlite3.connect(str(live), timeout=0)) as dst:
        try:
            dst.execute("BEGIN EXCLUSIVE")
            dst.execute("ROLLBACK")
        except sqlite3.OperationalError as e:
            raise RestoreError(_("db.restore_busy")) from e
        with closing(sqlite3.connect(str(backup))) as src:
            src.backup(dst)
    return pre


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


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Alembic filter: the FTS5 virtual table and its shadow tables are not in the models, so
    autogenerate must not propose dropping them."""
    return not (type_ == "table" and name and name.startswith("entity_fts"))


class SchemaTooNew(RuntimeError):
    """The library was last opened by a newer Rhizome (its schema revision is unknown here)."""


SCHEMA_FILE = "schema.json"


def _known_revision(engine: Engine, rev: str) -> bool:
    from alembic.script import ScriptDirectory

    try:
        return ScriptDirectory.from_config(_alembic_config(engine)).get_revision(rev) is not None
    except Exception:  # noqa: BLE001 - alembic raises several types for unknown ids
        return False


def _schema_too_new(settings: Settings, rev: str) -> SchemaTooNew:
    import json

    from .. import __version__
    from ..i18n import _

    by = "?"
    try:
        by = json.loads((settings.data_dir / SCHEMA_FILE).read_text("utf-8")).get("app_version", "?")
    except (OSError, ValueError):
        pass
    return SchemaTooNew(_("db.schema_too_new", revision=rev, version=by, current=__version__,
                          backups=str(settings.backups_dir)))


def _write_schema_marker(settings: Settings, rev: str | None) -> None:
    import json

    from .. import __version__

    p = settings.data_dir / SCHEMA_FILE
    data = {"revision": rev, "app_version": __version__}
    try:
        if not p.exists() or json.loads(p.read_text("utf-8")) != data:
            p.write_text(json.dumps(data), "utf-8")
    except (OSError, ValueError):
        pass


def _migration_engine(url: str) -> Engine:
    """Own connection for migrations: pysqlite does not put DDL in a transaction by itself, so a
    crash half-way would leave tables created while alembic_version still says the old revision.
    BEGIN IMMEDIATE makes the whole upgrade one transaction and serialises concurrent starters
    (app, CLI, Claude's MCP server). Foreign keys are off so batch table copies don't cascade."""
    from sqlalchemy.pool import NullPool

    engine = create_engine(url, poolclass=NullPool, connect_args={"timeout": 300})

    @event.listens_for(engine, "connect")
    def _connect(dbapi_conn, _rec):  # pragma: no cover - trivial
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        _enable_wal(cur, wait=300.0)
        cur.close()

    @event.listens_for(engine, "begin")
    def _begin(conn):  # pragma: no cover - trivial
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def init_db(settings: Settings | None = None) -> Engine:
    """Create or upgrade the schema to head: one transaction, serialised across processes, with a
    backup taken first (inside the lock, so concurrent starters make exactly one)."""
    from alembic import command
    from alembic.runtime.migration import MigrationContext

    settings = settings or get_settings()
    engine = get_engine(settings)
    head = head_revision(engine)
    cur = current_revision(engine)
    if cur is not None and cur != head and not _known_revision(engine, cur):
        raise _schema_too_new(settings, cur)  # before any backup: nothing to undo
    if cur == head:
        _write_schema_marker(settings, head)
        return engine
    if not is_sqlite(engine):
        with engine.begin() as conn:
            cfg = _alembic_config(engine)
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        return engine
    mig = _migration_engine(settings.db_url)
    try:
        with mig.begin() as conn:
            cur = MigrationContext.configure(conn).get_current_revision()  # under the lock
            if cur == head:
                return engine  # another process migrated while we waited
            if cur is not None:
                if not _known_revision(engine, cur):
                    raise _schema_too_new(settings, cur)
                path = backup_database(settings, tag=f"pre-{head}")
                log.info("backed up database to %s before migrating %s -> %s", path, cur, head)
            cfg = _alembic_config(engine)
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            bad = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise RuntimeError(f"migration left {len(bad)} dangling foreign keys; rolled back")
    finally:
        mig.dispose()
    engine.dispose()  # pooled connections may hold the old schema
    _write_schema_marker(settings, head)
    return engine


_fts_broken: set[str] = set()


def has_fts(session: Session) -> bool:
    """The FTS5 trigram table exists *and* this SQLite can query it. An old libsqlite3 (< 3.34,
    common on cluster login nodes) has no trigram tokenizer: the first query fails and keyword
    search falls back to alias substring matching for the rest of the process."""
    bind = session.get_bind()
    if bind.dialect.name != "sqlite" or str(bind.url) in _fts_broken:
        return False
    row = session.execute(
        text("select 1 from sqlite_master where type='table' and name='entity_fts'")
    ).first()
    return row is not None


def fts_unavailable(session: Session, error: Exception) -> None:
    """Remember that FTS queries fail on this connection's SQLite (called from the except branch)."""
    url = str(session.get_bind().url)
    if url not in _fts_broken:
        _fts_broken.add(url)
        log.warning("full-text search unavailable (SQLite %s, need >= 3.34 with FTS5 trigram): %s; "
                    "keyword search uses alias matching only", sqlite3.sqlite_version, error)
