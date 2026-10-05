# SPDX-License-Identifier: Apache-2.0
"""Operational details: pinned model snapshots are named by commit, update checks are daily and
quiet, new library folders are secured on Windows only, and the repo scripts behave."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_versioned_model_names_map_back_to_settings(tmp_path):
    from rhizome.ml.hf import versioned_name
    from rhizome.ml.registry import embedder_setting_for

    snap = tmp_path / "models--BAAI--bge-m3" / "snapshots" / "abcdef0123456789"
    assert versioned_name("bge-m3", snap) == "bge-m3@abcdef0"
    assert versioned_name("bge-m3", tmp_path / "local") == "bge-m3@local"
    assert embedder_setting_for("bge-m3@abcdef0") == "bge-m3" and embedder_setting_for("hashing-v1") == "hashing"
    assert embedder_setting_for("something-else@1") is None


def test_update_check_is_daily_and_quiet(settings, session, monkeypatch):
    from rhizome import jobs
    from rhizome.config import set_settings
    from rhizome.services.views import home_stats

    set_settings(settings.model_copy(update={"offline": False, "check_updates": True}))
    calls = []

    class R:
        status_code = 200

        def json(self):
            return {"tag_name": "v9.9.9", "html_url": "https://example.test/releases/v9.9.9"}

    monkeypatch.setattr("rhizome.external.verify.get", lambda url, retries=0: calls.append(url) or R())
    rec = jobs.maybe_check_updates()
    assert rec["newer"] and rec["latest"] == "9.9.9" and len(calls) == 1
    jobs.maybe_check_updates()
    assert len(calls) == 1  # within 24 h: no second request
    session.expire_all()
    assert home_stats(session)["update"]["latest"] == "9.9.9"
    monkeypatch.setattr("rhizome.external.verify.get", lambda url, retries=0: (_ for _ in ()).throw(OSError("no net")))
    assert jobs.maybe_check_updates(force=True)["latest"] == "9.9.9"  # a failure keeps the last record
    set_settings(settings.model_copy(update={"offline": True}))
    assert jobs.maybe_check_updates(force=True)["latest"] == "9.9.9" and len(calls) == 1
    assert jobs.version_tuple("v0.1.10") > jobs.version_tuple("0.1.9") and jobs.version_tuple("1.0") > jobs.version_tuple("0.9.9")


@pytest.mark.skipif(sys.platform == "win32", reason="icacls is exercised on Windows only")
def test_secure_dir_is_a_no_op_off_windows(tmp_path):
    from rhizome.system import outside_profile, secure_dir

    assert secure_dir(tmp_path / "new") is False
    assert isinstance(outside_profile(tmp_path), bool)


def _run(script, *args, stdin=""):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], input=stdin, text=True,
                          capture_output=True, cwd=ROOT)


def test_license_gate_handles_lists_and_empty_input():
    ok = json.dumps([{"Name": "a", "License": "MIT; GPL-3.0"}, {"Name": "b", "License": ["GPL-2.0", "Apache-2.0"]},
                     {"Name": "c", "License": "LGPL-2.1"}])
    assert _run("check_licenses.py", "pip", stdin=ok).returncode == 0
    bad = json.dumps({"x@1.0": {"licenses": "GPL-3.0"}, "y@2": {"licenses": ["AGPL-3.0", "GPL-2.0"]}})
    r = _run("check_licenses.py", "npm", stdin=bad)
    assert r.returncode == 1 and "x: GPL-3.0" in r.stdout and "y:" in r.stdout
    r = _run("check_licenses.py", "pip", stdin="")
    assert r.returncode != 0 and "empty" in (r.stderr + r.stdout)


def test_version_and_notice_scripts(tmp_path):
    assert _run("check_version.py").returncode == 0
    assert _run("check_version.py", "--expect", "0.0.0").returncode != 0
    pip = tmp_path / "pip.json"
    pip.write_text(json.dumps([{"Name": "numpy", "Version": "2.0", "License": "BSD", "URL": "https://numpy.org"},
                               {"Name": "pyinstaller", "Version": "6", "License": "GPL-2.0 with exception"}]), "utf-8")
    cargo = tmp_path / "cargo.json"
    cargo.write_text(json.dumps({"packages": [{"name": "tauri", "version": "2.0", "license": "MIT OR Apache-2.0",
                                               "repository": "https://github.com/tauri-apps/tauri"},
                                              {"name": "rhizome-desktop", "version": "0", "license": "Apache-2.0"}]}), "utf-8")
    out = tmp_path / "NOTICES.txt"
    r = _run("gen_third_party_notices.py", "--pip", str(pip), "--cargo", str(cargo), "-o", str(out))
    assert r.returncode == 0, r.stderr
    text = out.read_text("utf-8")
    assert "numpy 2.0" in text and "tauri 2.0" in text and "pyinstaller" not in text and "rhizome-desktop" not in text
    assert "Inter" in text and "Open Font License" in text
