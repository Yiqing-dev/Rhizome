# SPDX-License-Identifier: Apache-2.0
"""Logs per role, unhandled errors logged with an id, inbox state visible."""

import logging

from fastapi.testclient import TestClient

from conftest import example


def test_log_file_per_role_and_noisy_libraries_quiet(settings, monkeypatch):
    from rhizome import logging_setup

    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [h for h in root.handlers if not getattr(h, "_rhizome", False)])
    monkeypatch.setenv("RHIZOME_LOG_LEVEL", "debug")
    logging_setup.setup_logging(settings, role="mcp")
    logging.getLogger("rhizome.test").debug("hello from mcp")
    for h in root.handlers:
        h.flush()
    text = (settings.logs_dir / "rhizome-mcp.log").read_text("utf-8")
    assert "hello from mcp" in text and "[mcp " in text
    assert logging.getLogger("httpx").level == logging.WARNING
    for h in [h for h in root.handlers if getattr(h, "_rhizome", False)]:
        root.removeHandler(h)
        h.close()


def test_rotation_failure_does_not_lose_records(settings, monkeypatch):
    from rhizome.logging_setup import SafeRotatingFileHandler

    h = SafeRotatingFileHandler(settings.logs_dir / "x.log", maxBytes=10, backupCount=2, encoding="utf-8")
    monkeypatch.setattr("logging.handlers.RotatingFileHandler.doRollover",
                        lambda self: (_ for _ in ()).throw(PermissionError(13, "in use by another process")))
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "a fairly long message", None, None)
    h.emit(rec)
    h.emit(rec)  # rollover fails: still written, retried later
    h.close()
    assert (settings.logs_dir / "x.log").read_text("utf-8").count("a fairly long message") == 2


def test_unhandled_errors_are_logged_with_an_id(settings, monkeypatch, caplog):
    from rhizome.api.app import create_app
    from rhizome.services import views

    def boom(*a, **k):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(views, "home_stats", boom)
    app = create_app(settings, start_worker=False)
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    with caplog.at_level(logging.ERROR):
        r = c.get("/stats")
    assert r.status_code == 500 and r.json()["request_id"] in r.json()["detail"]
    assert any(r.json()["request_id"] in m and "/stats" in m for m in caplog.messages)


def test_inbox_status_lists_waiting_and_unrecognised_files(settings):
    from rhizome import inbox

    (settings.inbox / "notes.txt").write_text("my notes", "utf-8")
    (settings.inbox / "paper.yaml").write_text(example("light-spatial-domains.yaml"), "utf-8")
    inbox.scan()
    st = inbox.status()
    assert st["ignored"] == ["notes.txt"] and st["pending"] == [] and st["last_scan"]
