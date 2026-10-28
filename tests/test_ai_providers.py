"""Gemini, OpenAI (ChatGPT), Codex CLI and Gemini CLI in Fix code - stubbed: nothing here calls a provider."""

import io
import json
import types
import urllib.error

import pytest

from wishbridge import ai_providers as ap
from wishbridge.warehouses.ssis import ai


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.test-gemini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.delenv("WISHBRIDGE_HOSTED", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    ap._PICKED.clear()


class FakeHttp:
    """Answers urllib requests by URL; records what was sent."""

    def __init__(self, routes):
        self.routes, self.sent = routes, []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        self.sent.append((req.full_url, dict(req.header_items()), body))
        for part, answer in self.routes.items():
            if part in req.full_url:
                if isinstance(answer, Exception):
                    raise answer
                return io.BytesIO(json.dumps(answer).encode())
        raise AssertionError(f"unexpected request {req.full_url}")


def _http_error(code, message):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(json.dumps({"error": {"message": message}}).encode()))


def test_the_newest_models_are_picked():
    names = ["models/gemini-2.5-pro", "models/gemini-2.5-flash", "models/gemini-3-pro-preview",
             "models/gemini-2.0-flash-lite", "models/gemini-2.5-flash-image"]
    assert ap.pick_gemini(names, fast=False) == "gemini-3-pro-preview"
    assert ap.pick_gemini(names, fast=True) == "gemini-2.5-flash"
    assert ap.pick_gemini(["models/gemini-3-pro-preview", "models/gemini-3-pro"], fast=False) == "gemini-3-pro"
    ids = ["gpt-4o", "gpt-5", "gpt-5-mini", "gpt-5.1", "gpt-5.1-mini", "gpt-5-nano", "o3"]
    assert ap.pick_openai(ids, fast=False) == "gpt-5.1"
    assert ap.pick_openai(ids, fast=True) == "gpt-5.1-mini"


def test_gemini_answers_a_fix(keys, monkeypatch):
    http = FakeHttp({"/models?": {"models": [{"name": "models/gemini-2.5-flash",
                                              "supportedGenerationMethods": ["generateContent"]}]},
                     ":generateContent": {"candidates": [{"content": {"parts": [
                         {"text": "thinking...", "thought": True},
                         {"text": "<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\nNOTES:\n- fixed"}]},
                         "finishReason": "STOP"}]}})
    monkeypatch.setattr(ap.urllib.request, "urlopen", http)
    s = ai.ask_claude("SSIS", "notebook", "<DTS/>", "x = 1\n", [], "", "unused", via="gemini")
    assert s.status == "ok" and s.sql == "x = 2\n"
    url, headers, body = http.sent[-1]
    assert url.endswith("/models/gemini-2.5-flash:generateContent")
    assert headers.get("X-goog-api-key") == "AQ.test-gemini"
    assert "senior data engineer" in body["systemInstruction"]["parts"][0]["text"]


def test_openai_answers_a_fix(keys, monkeypatch):
    http = FakeHttp({"/models": {"data": [{"id": "gpt-5"}, {"id": "gpt-5-mini"}]},
                     "/chat/completions": {"choices": [{"message": {"content":
                         "<<<<<<< SEARCH\nx = 1\n=======\nx = 3\n>>>>>>> REPLACE"}, "finish_reason": "stop"}]}})
    monkeypatch.setattr(ap.urllib.request, "urlopen", http)
    s = ai.ask_claude("SSIS", "notebook", "<DTS/>", "x = 1\n", [], "", "unused", via="openai", fast=False)
    assert s.status == "ok" and s.sql == "x = 3\n"
    assert http.sent[-1][2]["model"] == "gpt-5" and http.sent[-1][1]["Authorization"] == "Bearer sk-test-openai"


@pytest.mark.parametrize("provider,code,message,says", [
    ("gemini", 400, "API key not valid. Please pass a valid API key.", "does not accept"),
    ("gemini", 429, "Resource has been exhausted (e.g. check quota).", "limit"),
    ("openai", 401, "Incorrect API key provided", "does not accept"),
    ("openai", 429, "You exceeded your current quota [insufficient_quota]", "no credits"),
])
def test_a_key_test_says_what_is_wrong(keys, monkeypatch, provider, code, message, says):
    monkeypatch.setattr(ap.urllib.request, "urlopen", FakeHttp({  # the request first: its address has /models too
        ("generateContent" if provider == "gemini" else "/chat/completions"): _http_error(code, message),
        "/models": {"models": [], "data": []}}))
    ok, msg = ap.test(provider, "k")
    assert ok is False and says in msg


def test_a_failed_answer_is_shown_not_raised(keys, monkeypatch):
    monkeypatch.setattr(ap.urllib.request, "urlopen", FakeHttp({"/models": {"data": []},
                                                               "/chat/completions": _http_error(401, "Incorrect API key")}))
    s = ai.ask_claude("SSIS", "notebook", "<DTS/>", "x = 1\n", [], "", "unused", via="openai")
    assert s.status == "failed" and "does not accept" in s.notes


def test_without_a_key_the_connection_says_so(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    backend, why = ai.assist_backend("gemini")
    assert backend is None and "paste it below" in why


def test_the_command_line_tools_are_laptop_only(monkeypatch):
    monkeypatch.setenv("WISHBRIDGE_HOSTED", "1")
    backend, why = ai.assist_backend("codex")
    assert backend is None and "laptop" in why


@pytest.mark.parametrize("via,program", [("codex", "codex"), ("gemini-cli", "gemini")])
def test_the_command_line_tools_answer_read_only(keys, monkeypatch, via, program):
    seen = {}

    class Proc:
        returncode = 0

        def __init__(self, cmd, **kw):
            seen["cmd"], seen["cwd"] = cmd, kw["cwd"]
            if via == "codex":
                out = cmd[cmd.index("--output-last-message") + 1]
                open(out, "w", encoding="utf-8").write("<<<<<<< SEARCH\nx = 1\n=======\nx = 4\n>>>>>>> REPLACE")

        def communicate(self, text, timeout=None):
            seen["prompt"] = text
            return ("" if via == "codex" else "<<<<<<< SEARCH\nx = 1\n=======\nx = 4\n>>>>>>> REPLACE"), ""

    monkeypatch.setattr(ap.shutil, "which", lambda name: f"C:/tools/{name}.cmd" if name == program else None)
    monkeypatch.setattr(ap.subprocess, "Popen", Proc)
    s = ai.ask_claude("SSIS", "notebook", "<DTS/>", "x = 1\n", [], "", "unused", via=via)
    assert s.status == "ok" and s.sql == "x = 4\n"
    assert "senior data engineer" in seen["prompt"] and "wishbridge-" in seen["cwd"]
    if via == "codex":
        assert seen["cmd"][1] == "exec" and "read-only" in seen["cmd"]


def test_a_tool_that_is_not_signed_in_says_how(keys, monkeypatch):
    class Proc:
        returncode = 1

        def __init__(self, cmd, **kw):
            pass

        def communicate(self, text, timeout=None):
            return "", "Error: not logged in. Please run codex login"

    monkeypatch.setattr(ap.shutil, "which", lambda name: "C:/tools/codex.cmd")
    monkeypatch.setattr(ap.subprocess, "Popen", Proc)
    s = ai.ask_claude("SSIS", "notebook", "<DTS/>", "x = 1\n", [], "", "unused", via="codex")
    assert s.status == "failed" and "codex login" in s.notes


def test_the_workspace_lists_every_chat_model_claude_first(monkeypatch):
    import databricks.sdk

    N = types.SimpleNamespace
    eps = [N(name="databricks-gpt-oss-120b", task="llm/v1/chat", config=N(served_entities=[])),
           N(name="databricks-gte-large-en", task="llm/v1/embeddings", config=N(served_entities=[])),
           N(name="databricks-claude-sonnet-4", task="llm/v1/chat", config=N(served_entities=[])),
           N(name="databricks-llama-4-maverick", task="llm/v1/chat", config=N(served_entities=[]))]
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda **kw: N(serving_endpoints=N(list=lambda: eps)))
    assert ai.databricks_claude_endpoints("DEFAULT") == ["databricks-claude-sonnet-4", "databricks-gpt-oss-120b",
                                                         "databricks-llama-4-maverick"]


def test_every_warehouse_offers_the_same_connections():
    import importlib
    from pathlib import Path

    root = Path(ai.__file__).parents[1]
    for w in sorted(p.name for p in root.iterdir() if (p / "ai.py").is_file()):
        mod = importlib.import_module(f"wishbridge.warehouses.{w}.ai")
        assert list(mod.VIA) == list(ai.VIA), w


def test_a_workspace_model_with_a_smaller_answer_limit_is_asked_again_within_it(monkeypatch):
    from wishbridge import hosting

    N = types.SimpleNamespace
    asked = []

    def query(name, max_tokens, messages):
        asked.append(max_tokens)
        if max_tokens > 25000:
            raise RuntimeError("max_new_tokens 32000 cannot be greater than max_output_tokens 25000.")
        return N(choices=[N(message=N(content="OK"), finish_reason="stop")])

    monkeypatch.setattr(hosting, "workspace_client", lambda **kw: N(serving_endpoints=N(query=query)))
    assert ai._ask_databricks("system", "user", "DEFAULT", "databricks-gpt-oss-120b") == "OK"
    assert asked == [32000, 25000]
    assert ai._answer_limit("ENDPOINT_NOT_FOUND: no such endpoint") is None
