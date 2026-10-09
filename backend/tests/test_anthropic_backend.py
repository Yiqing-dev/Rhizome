# SPDX-License-Identifier: Apache-2.0
"""The Anthropic API as an optional generative backend: structured-output requests for the three
judgements, every failure answered by the review queue, the key kept out of settings.json."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

anthropic = pytest.importorskip("anthropic")

from rhizome import secrets  # noqa: E402
from rhizome.inference import QueueBackend, get_backend, reset_backend  # noqa: E402
from rhizome.inference.anthropic_api import AnthropicBackend  # noqa: E402


class FakeMessages:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        nxt = self.answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if nxt == "refusal":
            return SimpleNamespace(stop_reason="refusal", content=[])
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(nxt))])


def fake_client(*answers):
    msgs = FakeMessages(answers)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def _status_error(cls, status):
    import httpx2 as httpx

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


def test_three_judgements_use_structured_output():
    client, msgs = fake_client({"relation": "about", "confidence": 0.9}, {"relation": "narrower", "confidence": 0.7},
                               {"q": "What does RootNet build?", "a": "Cell-type GRNs."})
    b = AnthropicBackend("claude-opus-5-5", "sk-test", client=client)
    assert b.classify_topic_relation("RootNet builds GRNs", "GRN inference") == ("about", 0.9)
    assert b.judge_breadth("GRN inference", "regulatory genomics") == ("narrower", 0.7)
    assert b.make_card("RootNet: tool that builds cell-type GRNs")[0].startswith("What")
    for call in msgs.calls:
        assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
        fmt = call["output_config"]["format"]
        assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
        assert call["output_config"]["effort"] == "low" and "thinking" not in call
    assert "TOPIC:" in msgs.calls[0]["messages"][0]["content"]


def test_failures_answer_none_and_pause_or_disable():
    client, msgs = fake_client("refusal", _status_error(anthropic.RateLimitError, 429),
                               {"relation": "none", "confidence": 1.0})
    b = AnthropicBackend(client=client)
    assert b.classify_topic_relation("x", "y") is None  # refusal: the queue decides
    assert b.classify_topic_relation("x", "y") is None  # rate limited
    assert b.last_error.startswith("RateLimitError") and b.classify_topic_relation("x", "y") is None  # paused
    assert len(msgs.calls) == 2
    b._paused_until = 0.0
    assert b.classify_topic_relation("x", "y") == ("none", 1.0)
    client, msgs = fake_client(_status_error(anthropic.AuthenticationError, 401), {"q": "a", "a": "b"})
    b = AnthropicBackend(client=client)
    assert b.make_card("x") is None and b.make_card("x") is None and len(msgs.calls) == 1  # disabled for good


class MemoryKeyring:
    """keyring backend for tests: the real stores need a desktop session."""
    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def delete_password(self, service, user):
        from keyring.errors import PasswordDeleteError

        if (service, user) not in self.store:
            raise PasswordDeleteError(user)
        del self.store[(service, user)]


@pytest.fixture()
def memory_keyring(monkeypatch):
    import keyring
    from keyring.backend import KeyringBackend

    cls = type("MemoryKeyring", (KeyringBackend,), dict(MemoryKeyring.__dict__))
    kr = cls()
    monkeypatch.setattr(keyring, "get_password", kr.get_password)
    monkeypatch.setattr(keyring, "set_password", kr.set_password)
    monkeypatch.setattr(keyring, "delete_password", kr.delete_password)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return kr


def test_secret_resolution_and_backend_selection(settings, memory_keyring, monkeypatch):
    assert secrets.secret_source("anthropic_api_key") is None and secrets.get_secret("anthropic_api_key") is None
    settings.inference_backend = "anthropic"
    reset_backend()
    assert isinstance(get_backend(), QueueBackend)  # selected but no key: the queue, not a crash
    assert secrets.set_secret("anthropic_api_key", " sk-stored ") == "keyring"
    assert secrets.get_secret("anthropic_api_key") == "sk-stored" and secrets.secret_source("anthropic_api_key") == "keyring"
    reset_backend()
    b = get_backend()
    assert isinstance(b, AnthropicBackend) and b._api_key == "sk-stored" and b.model == "claude-opus-5-5"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert secrets.secret_source("anthropic_api_key") == "env" and secrets.get_secret("anthropic_api_key") == "sk-env"
    settings.anthropic_model = "claude-sonnet-5-5"
    reset_backend()
    assert get_backend().model == "claude-sonnet-5-5" and get_backend()._api_key == "sk-env"
    secrets.set_secret("anthropic_api_key", None)
    secrets.set_secret("anthropic_api_key", None)  # deleting twice is fine
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert secrets.secret_source("anthropic_api_key") is None
    with pytest.raises(ValueError):
        secrets.set_secret("openai_api_key", "x")
    settings.inference_backend = "queue"
    reset_backend()


def test_secret_endpoint_and_settings(settings, library, session, memory_keyring):
    from rhizome.api.app import create_app

    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    assert c.get("/system").json()["secrets"] == {"anthropic_api_key": None}
    r = c.post("/settings/secret", json={"name": "anthropic_api_key", "value": "sk-ui"})
    assert r.status_code == 200 and r.json() == {"name": "anthropic_api_key", "source": "keyring"}
    assert "sk-ui" not in r.text.replace("sk-ui", "") and c.get("/system").json()["secrets"]["anthropic_api_key"] == "keyring"
    assert c.post("/settings/secret", json={"name": "nope", "value": "x"}).status_code == 422
    r = c.patch("/settings", json={"inference_backend": "anthropic", "anthropic_model": "claude-sonnet-5-5"})
    assert r.status_code == 200 and r.json()["anthropic_model"] == "claude-sonnet-5-5"
    assert "sk-ui" not in json.dumps(c.get("/settings").json())  # the key is not a setting
    assert (settings.data_dir / "settings.json").exists() and "sk-ui" not in (settings.data_dir / "settings.json").read_text()
    assert c.post("/settings/secret", json={"name": "anthropic_api_key", "value": None}).json()["source"] is None
    c.patch("/settings", json={"inference_backend": "queue"})


def test_cli_secret(settings, memory_keyring):
    from typer.testing import CliRunner

    from rhizome.cli import app

    r = CliRunner().invoke(app, ["--data-dir", str(settings.data_dir), "settings", "secret", "anthropic_api_key"],
                           input="sk-cli\n", catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "sk-cli" not in r.output and secrets.get_secret("anthropic_api_key") == "sk-cli"
    r = CliRunner().invoke(app, ["--data-dir", str(settings.data_dir), "settings", "secret", "anthropic_api_key", "--clear"],
                           catch_exceptions=False)
    assert r.exit_code == 0 and secrets.get_secret("anthropic_api_key") is None
    assert CliRunner().invoke(app, ["--data-dir", str(settings.data_dir), "settings", "secret", "other"]).exit_code == 2
