# SPDX-License-Identifier: Apache-2.0
"""Read-only snapshots for remote machines (e.g. HPC clusters that cannot reach this computer).

SQLite: a consistent copy via the online backup API (vectors live in the same file), switched to a
rollback journal so it can be opened read-only from any folder without -wal/-shm side files.
PostgreSQL: rows are copied into a fresh SQLite file through the shared SQLAlchemy models.
"""

from __future__ import annotations

import json
import logging
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, insert, select, text

from .. import __version__
from ..config import RemoteTarget, Settings
from ..db.models import Base
from ..db.session import copy_sqlite, get_engine, head_revision, is_sqlite
from ..pipeline.graph import FTS_DDL, rebuild_fts

log = logging.getLogger(__name__)
META_KEY = "snapshot_meta"
STALE_AFTER = timedelta(days=7)


def make_snapshot(settings: Settings, dst: Path) -> Path:
    engine = get_engine(settings)
    if is_sqlite(engine):
        copy_sqlite(Path(settings.db_url.split("sqlite:///", 1)[1]), dst)
    else:
        if dst.exists():
            dst.unlink()
        out = create_engine(f"sqlite:///{dst.as_posix()}")
        Base.metadata.create_all(out)
        with engine.connect() as src, out.begin() as tgt:
            for table in Base.metadata.sorted_tables:
                rows = [dict(r._mapping) for r in src.execute(select(table))]
                if rows:
                    tgt.execute(insert(table), rows)
            tgt.execute(text(FTS_DDL))
        # FTS rows: the same text the app indexes
        with out.begin() as tgt:
            rebuild_fts(tgt)
            tgt.execute(text("create table if not exists alembic_version (version_num varchar(32) primary key)"))
        out.dispose()
    _finish_snapshot(dst, head_revision(engine))
    return dst


def _finish_snapshot(path: Path, head: str | None) -> None:
    """Strip what the remote never needs (the query/text vector cache, vectors of other models,
    view history, job rows), stamp when/by what the snapshot was made, compact it and leave it as
    a plain rollback-journal file. A snapshot is a fraction of the live file's size."""
    meta = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "version": __version__,
            "head": head}
    with closing(sqlite3.connect(str(path))) as c:
        c.execute("delete from vector_cache")
        row = c.execute("select v from kv where k = 'embedding_model'").fetchone()
        model = (json.loads(row[0]) or {}).get("model") if row else None
        if model:
            c.execute("delete from embedding where model != ?", (model,))
        for table in ("access_log", "job"):
            if c.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone():
                c.execute(f"delete from {table}")  # noqa: S608 - table name from a fixed list
        c.execute("insert or replace into kv (k, v) values (?, ?)", (META_KEY, json.dumps(meta)))
        c.commit()
        c.execute("PRAGMA journal_mode=DELETE")
        c.execute("VACUUM")


def snapshot_meta(path: Path) -> dict:
    """{created_at, version, head, revision, index_model} of a snapshot ({} when unreadable)."""
    from ..db.session import sqlite_ro_uri

    try:
        with closing(sqlite3.connect(sqlite_ro_uri(Path(path).resolve()), uri=True)) as c:
            meta = {}
            row = c.execute("select v from kv where k = ?", (META_KEY,)).fetchone()
            if row:
                meta = json.loads(row[0]) or {}
            row = c.execute("select version_num from alembic_version").fetchone()
            meta["revision"] = row[0] if row else None
            row = c.execute("select v from kv where k = 'embedding_model'").fetchone()
            meta["index_model"] = (json.loads(row[0]) or {}).get("model") if row else None
            return meta
    except (sqlite3.Error, ValueError):
        return {}


def snapshot_warnings(path: Path, settings: Settings) -> list[str]:
    """What the remote user should know before trusting results from this snapshot: made by a
    different schema revision than this code, older than a week, or indexed with a model this
    machine cannot load. Localised, for stderr."""
    from ..i18n import _
    from ..ml.registry import NEEDS, _importable, embedder_setting_for

    meta = snapshot_meta(path)
    out: list[str] = []
    head = head_revision(get_engine(settings, read_only=True))
    if meta.get("revision") != head:
        out.append(_("cli.snapshot_revision", snapshot=meta.get("revision") or "?", code=head or "?",
                     version=meta.get("version") or "?", current=__version__))
    try:
        made = datetime.fromisoformat(meta["created_at"])
        age = datetime.now(timezone.utc) - made
        if age > STALE_AFTER:
            out.append(_("cli.snapshot_stale", days=age.days, created=made.date().isoformat()))
    except (KeyError, TypeError, ValueError):
        pass
    model = meta.get("index_model")
    if model and embedder_setting_for(model) != "hashing" and not _importable(NEEDS["embedder"]):
        out.append(_("cli.snapshot_model", model=model, module=NEEDS["embedder"]))
    return out


# ---- sync ----------------------------------------------------------------------------------------

def ssh_base(target: RemoteTarget) -> list[str]:
    opts: list[str] = []
    if target.control_path:
        # reuse an already-authenticated master connection (2FA servers)
        opts += ["-o", f"ControlPath={target.control_path}", "-o", "ControlMaster=auto", "-o", "ControlPersist=10m"]
    return opts + list(target.ssh_options)


def _rq(path: str) -> str:
    """Quote a remote path for the remote shell, keeping a leading ~ expandable."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        rest = path[2:].replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'"$HOME/{rest}"'
    return shlex.quote(path)


class SyncError(RuntimeError):
    pass


def sync_command(target: RemoteTarget) -> list[str]:
    """One SSH session (one password / 2FA prompt): the snapshot is streamed on stdin into a
    temporary file that is owner-only from the start (the library holds personal notes; cluster home
    directories are often group-readable), then renamed into place."""
    remote_dir = target.path.rsplit("/", 1)[0] if "/" in target.path else "."
    tmp_remote = target.path + ".uploading"
    script = (f"umask 077 && mkdir -p {_rq(remote_dir)} && cat > {_rq(tmp_remote)} && "
              f"chmod 600 {_rq(tmp_remote)} && mv -f {_rq(tmp_remote)} {_rq(target.path)}")
    return ["ssh", *ssh_base(target), "--", target.host, script]


def sync_warnings(target: RemoteTarget) -> list[str]:
    from ..i18n import _

    if target.control_path and sys.platform == "win32":
        return [_("cli.sync_controlmaster_win")]
    return []


def sync(settings: Settings, target: RemoteTarget, dry_run: bool = False) -> list[list[str]]:
    from ..i18n import _

    cmd = sync_command(target)
    if dry_run:
        return [cmd]
    with tempfile.TemporaryDirectory() as td:
        snap = make_snapshot(settings, Path(td) / "rhizome.db")
        try:
            with open(snap, "rb") as f:
                subprocess.run(cmd, stdin=f, check=True, timeout=target.timeout)
        except subprocess.TimeoutExpired as e:
            raise SyncError(_("cli.sync_timeout", host=target.host, seconds=target.timeout)) from e
        except subprocess.CalledProcessError as e:
            raise SyncError(_("cli.sync_failed", host=target.host, code=e.returncode)) from e
        except OSError as e:  # ssh not installed
            raise SyncError(_("cli.sync_failed", host=target.host, code=str(e))) from e
    return [cmd]
