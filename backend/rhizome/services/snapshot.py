# SPDX-License-Identifier: Apache-2.0
"""Read-only snapshots for remote machines (e.g. HPC clusters that cannot reach this computer).

SQLite: a consistent copy via the online backup API (vectors live in the same file).
PostgreSQL: rows are copied into a fresh SQLite file through the shared SQLAlchemy models.
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, insert, select, text

from ..config import RemoteTarget, Settings
from ..db.models import Base
from ..db.session import copy_sqlite, get_engine, is_sqlite
from ..pipeline.graph import FTS_DDL, rebuild_fts


def make_snapshot(settings: Settings, dst: Path) -> Path:
    engine = get_engine(settings)
    if is_sqlite(engine):
        copy_sqlite(Path(settings.db_url.split("sqlite:///", 1)[1]), dst)
        return dst
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
    return dst


def ssh_base(target: RemoteTarget) -> list[str]:
    opts: list[str] = []
    if target.control_path:
        # reuse an already-authenticated master connection (2FA servers)
        opts += ["-o", f"ControlPath={target.control_path}", "-o", "ControlMaster=auto", "-o", "ControlPersist=10m"]
    return opts + list(target.ssh_options)


def _rq(path: str) -> str:
    """Quote a remote path for the remote shell, keeping a leading ~/ expandable."""
    if path.startswith("~/"):
        rest = path[2:].replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'"$HOME/{rest}"'
    return shlex.quote(path)


def sync(settings: Settings, target: RemoteTarget, dry_run: bool = False) -> list[list[str]]:
    with tempfile.TemporaryDirectory() as td:
        snap = make_snapshot(settings, Path(td) / "rhizome.db")
        remote_dir = target.path.rsplit("/", 1)[0] if "/" in target.path else "."
        tmp_remote = target.path + ".uploading"
        # the snapshot holds the whole library, personal ideas included: on a shared cluster it is
        # readable by the owner only (the upload target is created 600 before scp fills it, and
        # new folders 700; existing folders are left as they are)
        cmds = [
            ["ssh", *ssh_base(target), target.host,
             f"umask 077 && mkdir -p {_rq(remote_dir)} && : > {_rq(tmp_remote)} && chmod 600 {_rq(tmp_remote)}"],
            ["scp", *ssh_base(target), str(snap), f"{target.host}:{tmp_remote}"],
            ["ssh", *ssh_base(target), target.host,
             f"chmod 600 {_rq(tmp_remote)} && mv {_rq(tmp_remote)} {_rq(target.path)}"],
        ]
        if not dry_run:
            for c in cmds:
                subprocess.run(c, check=True)
        return cmds
