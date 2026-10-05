# SPDX-License-Identifier: Apache-2.0
"""Backups: a daily one, pruned per kind, in a configurable folder; rebuild never fails because of one."""

import os
import time

import pytest

from rhizome.db import session as dbs


def _fake(settings, tag, age_h, stamp):
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    p = settings.backups_dir / f"rhizome-{tag}-20260101-{stamp:06d}.db"
    p.write_bytes(b"x")
    t = time.time() - age_h * 3600
    os.utime(p, (t, t))
    return p


def test_prune_keeps_newest_per_kind_and_all_manual(settings):
    for i in range(6):
        _fake(settings, "pre-rebuild", i, i)
        _fake(settings, "manual", i, 100 + i)
        _fake(settings, "pre-0002", i, 200 + i)
    for i in range(20):
        _fake(settings, "daily", 24 * i, 300 + i)
    stale = settings.backups_dir / "x.db.tmp"
    stale.write_bytes(b"")
    os.utime(stale, (time.time() - 7200, time.time() - 7200))
    dbs.prune_backups(settings)
    kinds = [dbs._backup_kind(p) for p in dbs.list_backups(settings)]
    assert kinds.count("pre-rebuild") == 3 and kinds.count("manual") == 6
    assert kinds.count("pre-migration") == 5 and kinds.count("daily") == settings.backup_keep_daily
    assert not stale.exists()
    newest_daily = [p for p in dbs.list_backups(settings) if dbs._backup_kind(p) == "daily"][0]
    assert newest_daily.name.endswith("000300.db")  # the youngest ones survive


def test_daily_backup_once_per_20_hours(settings):
    assert dbs.maybe_daily_backup(settings) is not None
    assert dbs.maybe_daily_backup(settings) is None
    for p in dbs.list_backups(settings):
        t = time.time() - 21 * 3600
        os.utime(p, (t, t))
    assert dbs.maybe_daily_backup(settings) is not None


def test_backup_dir_is_configurable_and_must_be_absolute(settings, tmp_path):
    from rhizome.config import Settings

    other = tmp_path / "其他 硬盘" / "Rhizome 备份"
    st = Settings(data_dir=settings.data_dir, backup_dir=other)
    path = dbs.backup_database(st, tag="manual")
    assert path.parent == other and path.exists()
    with pytest.raises(ValueError):
        Settings(data_dir=settings.data_dir, backup_dir="relative/dir")


def test_rebuild_backs_up_once_per_burst_and_survives_a_failed_backup(library, session, settings, monkeypatch):
    from rhizome.pipeline import rebuild as rb

    rb.rebuild(session, backup=True)
    rb.rebuild(session, backup=True)
    assert [dbs._backup_kind(p) for p in dbs.list_backups(settings)].count("pre-rebuild") == 1

    for p in dbs.list_backups(settings):
        p.unlink()

    def full_disk(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(rb, "backup_database", full_disk)
    out = rb.rebuild(session, backup=True)
    assert out["entities"] > 0 and out["warnings"]


def test_system_info_reports_backups(settings):
    from rhizome.system import info

    dbs.backup_database(settings, tag="daily")
    b = info()["backups"]
    assert b["count"] == 1 and b["last_daily"] and b["bytes"] > 0
