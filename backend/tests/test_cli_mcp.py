# SPDX-License-Identifier: Apache-2.0
import json

from typer.testing import CliRunner

from conftest import EXAMPLES
from rhizome.cli import app

runner = CliRunner()


def _rhz(settings, *args):
    return runner.invoke(app, ["--data-dir", str(settings.data_dir), *args], catch_exceptions=False)


def test_cli_end_to_end(settings, tmp_path):
    r = _rhz(settings, "ingest", str(EXAMPLES / "deep-grn-atlas.yaml"), str(EXAMPLES / "light-spatial-domains.yaml"))
    assert r.exit_code == 0, r.output
    assert "work:doi:10.5555/rhz.example.0001" in r.output
    r = _rhz(settings, "--json", "search", "RootNet", "--type", "method")
    assert json.loads(r.output)[0]["name"] == "RootNet"
    r = _rhz(settings, "data", "GSE999001")
    assert "GEO" in r.output and "A single-nucleus" in r.output
    r = _rhz(settings, "get", "dataset:GSE999001")
    assert "produces" in r.output
    script = tmp_path / "analysis.py"
    script.write_text("import scanpy\n# spatial domain detection on Stereo-seq spots\n", "utf-8")
    r = _rhz(settings, "--json", "recall", "--from-file", str(script))
    assert r.exit_code == 0
    r = _rhz(settings, "queue")
    assert r.exit_code == 0
    r = _rhz(settings, "rebuild", "--no-backup")
    assert "entities" in r.output
    out = tmp_path / "vocab.yaml"
    assert _rhz(settings, "vocab", "-o", str(out)).exit_code == 0 and out.exists()
    snap = tmp_path / "remote" / "rhizome.db"
    assert _rhz(settings, "snapshot", str(snap)).exit_code == 0
    r = runner.invoke(app, ["--snapshot", str(snap), "--json", "search", "DomainGAT"])
    assert r.exit_code == 0 and json.loads(r.output)[0]["name"] == "DomainGAT"
    r = runner.invoke(app, ["--snapshot", str(snap), "ingest", str(EXAMPLES / "light-scenic-benchmark.yaml")])
    assert r.exit_code != 0  # snapshots are read-only


def test_cli_invalid_and_zh(settings):
    r = _rhz(settings, "--lang", "zh_CN", "ingest", str(EXAMPLES / "invalid" / "missing-evidence.yaml"))
    assert r.exit_code == 1 and "校验失败" in r.output
    r = _rhz(settings, "rxf", "validate", str(EXAMPLES / "light-scenic-benchmark.yaml"))
    assert r.exit_code == 0


def test_sync_dry_run(settings):
    from rhizome.config import RemoteTarget, save_settings

    st = settings.model_copy(update={"remotes": [RemoteTarget(name="hpc", host="me@login.example.edu",
                                                              control_path="~/.ssh/cm-%r@%h:%p")]})
    save_settings(st)
    r = _rhz(settings, "sync", "hpc", "--dry-run")
    assert r.exit_code == 0, r.output
    assert "ControlPath=~/.ssh/cm-%r@%h:%p" in r.output and "scp" in r.output


def test_mcp_tools_local(settings):
    import rhizome.mcp_server as m
    from rhizome.client import LocalClient

    m._client = LocalClient(settings)
    res = json.loads(m.rhz_ingest((EXAMPLES / "deep-grn-atlas.yaml").read_text("utf-8")))
    assert res["ok"]
    bad = json.loads(m.rhz_ingest("rxf_version: 1\npaper: {}\n"))
    assert not bad["ok"] and bad["report"]
    assert json.loads(m.rhz_search("RootNet", types="method"))[0]["name"] == "RootNet"
    card = json.loads(m.rhz_get("dataset:GSE999001"))
    assert card["external_id"] == "GSE999001"
    assert isinstance(json.loads(m.rhz_recall("accessibility priors for sparse data")), list)
    q = json.loads(m.rhz_queue())
    assert "items" in q
    assert "pairs" in json.loads(m.rhz_digest())
    tools = {t.name for t in m.mcp._tool_manager.list_tools()}
    assert {"rhz_ingest", "rhz_recall", "rhz_search", "rhz_get", "rhz_related", "rhz_queue", "rhz_decide"} <= tools
    m._client = None


def test_mcp_follows_app_restarts(settings, monkeypatch):
    """Claude Desktop keeps the MCP process for its whole session; the app gets a new port and token
    on every start. A changed marker/token re-resolves the client; a refused connection or a 401
    reconnects once."""
    import httpx

    from rhizome import mcp_server

    made = []

    class Fake:
        def __init__(self, n):
            self.n = n

        def stats(self):
            if self.n == 1:
                raise httpx.ConnectError("refused")
            return {"client": self.n}

    def fake_connect():
        made.append(len(made))
        return Fake(len(made) - 1)

    monkeypatch.setattr(mcp_server, "connect", fake_connect)
    monkeypatch.setattr(mcp_server, "_client", None)
    monkeypatch.setattr(mcp_server, "_signature", None)
    assert mcp_server._call(lambda c: c.stats()) == {"client": 0}
    assert mcp_server._call(lambda c: c.stats()) == {"client": 0}  # nothing changed: same client
    (settings.data_dir / "server.json").write_text('{"url": "http://127.0.0.1:50001", "pid": 1}', "utf-8")
    # the app (re)started: new client (#1) refuses the connection -> one reconnect (#2)
    assert mcp_server._call(lambda c: c.stats()) == {"client": 2}
    assert len(made) == 3

    def unauthorized(c):
        if c.n == 2:
            req = httpx.Request("GET", "http://x")
            raise httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))
        return {"client": c.n}

    assert mcp_server._call(unauthorized) == {"client": 3}
