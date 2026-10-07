"""AI fix suggestions with a stand-in Claude client (no API key or network needed)."""

from types import SimpleNamespace

import anthropic
import pytest

from wishbridge import ai
from wishbridge.rules import Finding

OPEN = [Finding("system-variable", "error", 3, "@@ROWCOUNT is not supported")]


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


def fake_client(stop_reason="end_turn", text="```sql\nSELECT 1;\n```\nNOTES:\n- replaced @@ROWCOUNT\n"):
    calls = []

    def stream(**kwargs):
        calls.append(kwargs)
        blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
        return FakeStream(SimpleNamespace(stop_reason=stop_reason, content=blocks))

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    return client, calls


@pytest.fixture
def use_client(monkeypatch):
    def install(**kw):
        client, calls = fake_client(**kw)
        monkeypatch.setattr(ai, "_client", lambda: (anthropic, client))
        return calls
    return install


def test_suggestion_parsed_and_request_shape(use_client):
    calls = use_client()
    s = ai.suggest_fix("MS SQL Server", "SELECT @@ROWCOUNT", "SELECT @@ROWCOUNT()", OPEN, "claude-opus-5-5")
    assert (s.status, s.sql, s.notes) == ("ok", "SELECT 1;\n", "- replaced @@ROWCOUNT")
    req = calls[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in req["betas"]
    user = req["messages"][0]["content"]
    assert "<original_ms_sql_server>" in user and "line 3 [error] system-variable" in user


def test_refusal_is_reported(use_client):
    use_client(stop_reason="refusal", text="")
    s = ai.suggest_fix("Oracle", "x", "y", OPEN, "claude-opus-5-5")
    assert s.status == "failed" and "declined" in s.notes


def test_truncated_answer_is_flagged(use_client):
    use_client(stop_reason="max_tokens")
    s = ai.suggest_fix("Oracle", "x", "y", OPEN, "claude-opus-5-5")
    assert s.status == "ok" and s.notes.startswith("WARNING: response was truncated")


def test_no_sql_block(use_client):
    use_client(text="I cannot see a problem.")
    assert ai.suggest_fix("Oracle", "x", "y", OPEN, "claude-opus-5-5").status == "failed"


def test_preflight_without_credentials_gives_clear_error(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", "/nonexistent-wishbridge-test")
    with pytest.raises(RuntimeError, match="need Claude credentials"):
        ai.preflight()


def test_nothing_to_fix_skips_the_call(use_client):
    calls = use_client()
    s = ai.suggest_fix("Oracle", "x", "y", [Finding("schema-map", "info", 1, "mapped", True)], "claude-opus-5-5")
    assert s.status == "skipped" and calls == []
