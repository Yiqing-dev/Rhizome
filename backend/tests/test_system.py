# SPDX-License-Identifier: Apache-2.0
"""Desktop integration: data-dir pointer / portable mode, Claude Desktop config merge, server marker."""

import json
import sys

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolate every OS location the module touches."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RHIZOME_DATA_DIR", raising=False)
    return tmp_path


def test_pointer_file_and_env_precedence(home, monkeypatch):
    from rhizome.config import default_data_dir, platform_data_dir, set_data_dir_pointer

    assert default_data_dir() == platform_data_dir()
    set_data_dir_pointer(home / "D" / "Rhizome")
    assert default_data_dir() == (home / "D" / "Rhizome").resolve()
    monkeypatch.setenv("RHIZOME_DATA_DIR", str(home / "env"))
    assert default_data_dir() == home / "env"
    monkeypatch.delenv("RHIZOME_DATA_DIR")
    set_data_dir_pointer(None)
    assert default_data_dir() == platform_data_dir()


def test_portable_mode(home, monkeypatch):
    from rhizome import config

    install = home / "Programs" / "Rhizome"
    (install / "server").mkdir(parents=True)
    (install / "portable").write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "server" / "rhz.exe"))
    assert config.default_data_dir() == install / "data"


def test_move_data_dir_copies_library(home, settings, library, session):
    from rhizome import config, system

    session.commit()
    target = home / "elsewhere" / "Rhizome"
    out = system.move_data_dir(str(target))
    assert out["copied"] and out["restart_required"]
    assert (target / "rhizome.db").exists() and (target / "raw").is_dir()
    assert config.default_data_dir() == target.resolve()
    with pytest.raises(ValueError):
        system.move_data_dir("relative/path")
    (home / "junk").mkdir()
    (home / "junk" / "x.txt").write_text("x")
    with pytest.raises(ValueError):  # non-empty folder that is not a library
        system.move_data_dir(str(home / "junk"))


def test_claude_desktop_merge_keeps_other_servers(home, settings, monkeypatch):
    from rhizome import system

    monkeypatch.setattr(sys, "platform", "win32")
    cfg = home / "Roaming" / "Claude" / "claude_desktop_config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    msix = home / "Local" / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude"
    msix.mkdir(parents=True)
    out = system.install_claude_desktop()
    assert len(out["written"]) == 2
    data = json.loads(cfg.read_text())
    assert data["theme"] == "dark" and "other" in data["mcpServers"]
    assert data["mcpServers"]["rhizome"]["args"][-1] == "mcp"
    assert cfg.with_suffix(".json.bak").exists()
    assert json.loads((msix / "claude_desktop_config.json").read_text())["mcpServers"]["rhizome"]


def test_frozen_app_points_claude_at_itself(home, settings, monkeypatch):
    from rhizome import system

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Rhizome\server\rhz.exe")
    entry = system.claude_desktop_snippet()["mcpServers"]["rhizome"]
    assert entry["command"].endswith("rhz.exe") and entry["args"] == ["mcp"]
    assert entry["env"]["RHIZOME_DATA_DIR"] == str(settings.data_dir)  # non-default dir is passed along


def test_server_marker_lets_clients_find_a_random_port(settings):
    from rhizome import system

    system.write_server_marker("http://127.0.0.1:51234")
    assert system.running_server_url() == "http://127.0.0.1:51234"
    system.clear_server_marker()
    assert system.running_server_url() is None


def test_token_from_shell_env(settings, monkeypatch):
    from rhizome.api.app import get_or_create_token, token_path

    monkeypatch.setenv("RHIZOME_API_TOKEN", "from-the-shell")
    assert get_or_create_token(settings) == "from-the-shell"
    assert token_path(settings).read_text() == "from-the-shell"  # CLI / MCP can authenticate


def test_system_endpoints(settings):
    from fastapi.testclient import TestClient

    from rhizome.api.app import create_app

    app = create_app(settings, start_worker=False, token="t")
    c = TestClient(app, headers={"Authorization": "Bearer t"})
    info = c.get("/system").json()
    assert info["data_dir"] == str(settings.data_dir) and "claude_desktop" in info
    assert c.post("/system/open/nowhere").status_code == 422


def test_backend_exits_when_parent_dies(settings, tmp_path):
    """`serve` started with RHIZOME_PARENT_PID stops by itself when that process is gone."""
    import os
    import socket
    import subprocess
    import time

    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    env = dict(os.environ, RHIZOME_DATA_DIR=str(tmp_path / "d"), RHIZOME_OFFLINE="1",
               RHIZOME_PARENT_PID=str(parent.pid), RHIZOME_API_TOKEN="t")
    srv = subprocess.Popen([sys.executable, "-m", "rhizome.cli", "serve", "--port", str(port)], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.3)
        assert srv.poll() is None
        parent.kill()
        parent.wait()
        assert srv.wait(timeout=15) == 0
    finally:
        for p in (parent, srv):
            if p.poll() is None:
                p.kill()


@pytest.mark.parametrize("dirname", ["D:drive-like", "100% library", "中文 资料库"])
def test_data_dir_with_windows_like_characters(tmp_path, dirname, monkeypatch):
    """Windows paths contain ':' (and users pick folders with '%' or spaces); migrations,
    normal use and read-only snapshot mode must all cope."""
    from rhizome.config import Settings, set_settings
    from rhizome.db.session import dispose_all, init_db, session_scope
    from rhizome.pipeline.ingest import ingest_text

    from conftest import example

    st = Settings(data_dir=tmp_path / dirname, offline=True)
    set_settings(st)
    st.ensure_dirs()
    dispose_all()
    init_db(st)
    with session_scope() as s:
        assert ingest_text(s, example("light-spatial-domains.yaml"), "x.yaml").ok
    with session_scope(read_only=True) as s:
        from rhizome.services.search import search

        assert search(s, "DomainGAT")
    dispose_all()
