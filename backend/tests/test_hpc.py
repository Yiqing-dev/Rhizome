# SPDX-License-Identifier: Apache-2.0
"""Remote use against a read-only snapshot (HPC login nodes): snapshots open from anywhere,
write commands refuse, settings survive, old SQLite falls back, sync is one ssh session."""

import json
import os
import sqlite3
import stat
import sys

import pytest
from typer.testing import CliRunner

from conftest import EXAMPLES
from rhizome.cli import app

runner = CliRunner()


@pytest.fixture()
def snapshot(settings, tmp_path, library):
    from rhizome.services.snapshot import make_snapshot

    return make_snapshot(settings, tmp_path / "remote" / "rhizome.db")


def test_snapshot_is_rollback_journal_with_metadata(snapshot, settings):
    from rhizome.db.session import get_engine, head_revision, is_wal_file
    from rhizome.services.snapshot import snapshot_meta

    head = snapshot.read_bytes()[:20]
    assert (head[18], head[19]) == (1, 1) and not is_wal_file(snapshot)
    meta = snapshot_meta(snapshot)
    assert meta["revision"] == head_revision(get_engine(settings)) == meta["head"]
    assert meta["created_at"] and meta["version"] and meta["index_model"] == "hashing-v1"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs a directory the user cannot write")
def test_snapshot_opens_from_a_read_only_folder(snapshot):
    folder = snapshot.parent
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        r = runner.invoke(app, ["--snapshot", str(snapshot), "--json", "search", "DomainGAT"])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)[0]["name"] == "DomainGAT"
    finally:
        folder.chmod(stat.S_IRWXU)
    assert sorted(p.name for p in folder.iterdir()) == ["rhizome.db"]  # no -wal / -shm left behind


def test_snapshot_leaves_no_side_files(snapshot):
    r = runner.invoke(app, ["--snapshot", str(snapshot), "--json", "search", "DomainGAT"])
    assert r.exit_code == 0, r.output
    assert sorted(p.name for p in snapshot.parent.iterdir()) == ["rhizome.db"]


def test_write_commands_refuse_a_snapshot(snapshot, tmp_path, monkeypatch):
    home = tmp_path / "_home"
    for args in (["ingest", str(EXAMPLES / "light-scenic-benchmark.yaml")], ["rebuild"], ["nightly"],
                 ["backup"], ["sync", "hpc"], ["settings", "set", "language", "zh_CN"], ["watch"]):
        r = runner.invoke(app, ["--snapshot", str(snapshot), *args])
        assert r.exit_code == 2, (args, r.output)
    assert not list(home.rglob("*.db")), "no library was created on the remote machine"
    before = snapshot.stat()
    out = tmp_path / "vocab.yaml"
    assert runner.invoke(app, ["--snapshot", str(snapshot), "vocab", "-o", str(out)]).exit_code == 0
    assert out.read_text("utf-8").startswith("# rhizome-vocab.yaml")
    diag = tmp_path / "diag.zip"
    r = runner.invoke(app, ["--snapshot", str(snapshot), "diag", "-o", str(diag)])
    assert r.exit_code == 0 and "SQLite" in r.output
    import zipfile

    info = json.loads(zipfile.ZipFile(diag).read("info.json"))
    assert info["stats"]["entities"]["work"] == 3 and info["snapshot"] == str(snapshot)  # read from the snapshot
    assert snapshot.stat().st_mtime == before.st_mtime and not list(home.rglob("*.db"))


def test_no_library_is_an_error_not_a_new_one(tmp_path):
    r = runner.invoke(app, ["search", "GRN"])
    assert r.exit_code == 2 and "RHIZOME_SNAPSHOT" in r.output
    assert not list((tmp_path / "_home").rglob("rhizome.db"))
    r = runner.invoke(app, ["ingest", str(EXAMPLES / "light-scenic-benchmark.yaml")])  # writes create it
    assert r.exit_code == 0, r.output


def test_snapshot_keeps_language_and_warns_on_old_or_foreign_snapshots(snapshot, monkeypatch):
    r = runner.invoke(app, ["--snapshot", str(snapshot), "--lang", "zh_CN", "data", "GSE000000"])
    assert r.exit_code == 1 and "未找到" in r.output
    r = runner.invoke(app, ["--snapshot", str(snapshot), "search", "DomainGAT"])
    assert "snapshot" not in r.output.lower()  # fresh, same revision: no warning
    with sqlite3.connect(snapshot) as c:
        c.execute("update kv set v = ? where k = 'snapshot_meta'",
                  (json.dumps({"created_at": "2020-01-01T00:00:00+00:00", "version": "0.0.1", "head": "x"}),))
        c.execute("update alembic_version set version_num = 'ffff'")
    r = runner.invoke(app, ["--snapshot", str(snapshot), "--lang", "zh_CN", "search", "DomainGAT"])
    assert "天" in r.output and "ffff" in r.output and "0.0.1" in r.output
    assert sorted(p.name for p in snapshot.parent.iterdir()) == ["rhizome.db"]


def test_snapshot_follows_the_index_model(snapshot, settings):
    from rhizome.client import connect

    with sqlite3.connect(snapshot) as c:
        c.execute("update kv set v = ? where k = 'embedding_model'", (json.dumps({"model": "bge-m3"}),))
    from rhizome.services.snapshot import snapshot_warnings

    warned = snapshot_warnings(snapshot, settings)
    assert any("bge-m3" in w for w in warned)
    from rhizome.config import get_settings

    connect(snapshot=snapshot)
    assert get_settings().embedder == "bge-m3" and get_settings().language == settings.language


def test_keyword_search_survives_an_old_sqlite(library, session, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from rhizome.db import session as dbs
    from rhizome.services.search import keyword_ids

    real = session.execute

    def fail_fts(stmt, *a, **k):
        if "entity_fts match" in str(stmt):
            raise OperationalError("select", {}, Exception("no such tokenizer: trigram"))
        return real(stmt, *a, **k)

    monkeypatch.setattr(session, "execute", fail_fts)
    monkeypatch.setattr(dbs, "_fts_broken", set())
    assert keyword_ids(session, "DomainGAT", ["method"])  # alias match instead
    assert not dbs.has_fts(session)


def test_sync_is_one_ssh_session(settings, monkeypatch):
    from rhizome.config import RemoteTarget
    from rhizome.services import snapshot as sn

    t = RemoteTarget(name="hpc", host="me@login.example.edu", path="~/.rhizome/rhizome.db",
                     control_path="~/.ssh/cm-%r@%h:%p", timeout=7)
    (cmd,) = sn.sync(settings, t, dry_run=True)
    assert cmd[0] == "ssh" and "scp" not in cmd and cmd[-2] == "me@login.example.edu"
    script = cmd[-1]
    assert script.startswith("umask 077 && mkdir -p \"$HOME/.rhizome\" && cat > \"$HOME/.rhizome/rhizome.db.uploading\"")
    assert "chmod 600" in script and script.endswith('mv -f "$HOME/.rhizome/rhizome.db.uploading" "$HOME/.rhizome/rhizome.db"')
    assert "ControlPath=~/.ssh/cm-%r@%h:%p" in cmd
    assert sn._rq("~") == '"$HOME"' and sn._rq("/data/a b/x.db") == "'/data/a b/x.db'"

    calls = []

    def fake_run(c, stdin=None, check=True, timeout=None):
        calls.append((c, stdin.read(16), timeout))
        import subprocess

        raise subprocess.CalledProcessError(255, c)

    monkeypatch.setattr(sn.subprocess, "run", fake_run)
    with pytest.raises(sn.SyncError, match="255"):
        sn.sync(settings, t)
    assert calls[0][1].startswith(b"SQLite format 3") and calls[0][2] == 7

    monkeypatch.setattr(sys, "platform", "win32")
    assert sn.sync_warnings(t) and not sn.sync_warnings(RemoteTarget(name="x", host="h"))


@pytest.mark.parametrize("acc,db,expect,absent", [
    ("SRR123456", "SRA", "prefetch SRR123456", "filereport"),
    ("PRJNA600001", "SRA", "filereport?accession=PRJNA600001", "prefetch PRJNA"),
    ("PRJEB1234", "ENA", "filereport?accession=PRJEB1234", "fasterq"),
    ("SRP000123", None, "Traces/study/?acc=SRP000123", "fasterq"),
    ("GSE999001", "GEO", "geo/series/GSE999nnn/GSE999001/suppl", "prefetch GSE"),
    ("GSE12", "GEO", "geo/series/GSEnnn/GSE12/suppl", "prefetch"),
    ("GSM4000001", "GEO", "acc.cgi?acc=GSM4000001", "suppl"),
    ("10.5281/zenodo.123456", None, "zenodo.org/records/123456", "Search"),
    ("123456", "Zenodo", "zenodo.org/records/123456", "Search"),
    ("E-MTAB-1234", "ArrayExpress", "arrayexpress/studies/E-MTAB-1234", "prefetch"),
    ("HRA000123", "GSA", "ngdc.cncb.ac.cn", "prefetch"),
])
def test_download_hints(acc, db, expect, absent):
    from rhizome.services.datasets import download_hint

    hint = download_hint(acc, db)
    assert expect in hint and absent not in hint, hint
