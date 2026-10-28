"""Fix code's 'Ask Claude': API key first, else Claude Code with no tools; a person always decides."""

import json
import subprocess

from wishbridge.warehouses.ssis import ai


def test_parse_answer_takes_the_fenced_file_and_notes():
    s = ai.parse_answer("Here:\n```python\nprint(1)\n```\nNOTES:\n- removed the missing procedure call")
    assert s.status == "ok" and s.sql == "print(1)\n" and s.notes.startswith("- removed")
    assert ai.parse_answer("I cannot do that").status == "failed"


def test_backend_prefers_the_api_key_then_claude_code(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ai.shutil, "which", lambda name: None)
    assert ai.assist_backend()[0] is None
    monkeypatch.setattr(ai.shutil, "which", lambda name: "/usr/local/bin/claude")
    assert ai.assist_backend()[0] == "claude-code"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert ai.assist_backend()[0] == "api"


def _claude_code(monkeypatch, answer: dict, signed_in: bool = True):
    """Claude Code stand-in: the request (Popen) answers `answer`; `claude auth status` (run) says signed_in."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ai.shutil, "which", lambda name: "/usr/local/bin/claude")
    seen = {}

    class FakePopen:
        returncode = 0

        def __init__(self, cmd, **kw):
            seen.update(cmd=cmd, **kw)

        def communicate(self, text=None, timeout=None):
            seen["input"] = text
            return json.dumps(answer) + "\n", ""

    monkeypatch.setattr(ai.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(ai.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, json.dumps({"loggedIn": signed_in}), ""))
    return seen


def test_claude_code_runs_with_no_tools_in_an_empty_folder(monkeypatch, tmp_path):
    seen = _claude_code(monkeypatch, {"is_error": False, "result": "```sql\nSELECT 1;\n```\nNOTES:\n- fixed"})
    s = ai.ask_claude("SSIS", "sql", "<DTS/>", "SELECT 0;", ["line 1 [error]: x"], "catalog is oltp_fed",
                      "claude-opus-5-5")
    assert s.status == "ok" and s.sql == "SELECT 1;\n"
    cmd = seen["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd and "-p" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert "catalog is oltp_fed" in seen["input"] and "<DTS/>" in seen["input"]
    assert "wishbridge-claude-" in seen["cwd"]


def test_claude_code_not_signed_in_says_what_to_do(monkeypatch):
    _claude_code(monkeypatch, {"is_error": True, "result": "Failed to authenticate: OAuth session expired"},
                 signed_in=False)
    s = ai.ask_claude("SSIS", "notebook", "", "x = 1", [], "", "claude-opus-5-5")
    assert s.status == "signin" and "Sign in again" in s.notes


def test_the_real_answer_wins_over_auth_status(monkeypatch):
    # seen on a real computer: `claude auth status` says signed in, the request says "Not logged in"
    _claude_code(monkeypatch, {"is_error": True, "result": "Not logged in · Please run /login"}, signed_in=True)
    s = ai.ask_claude("SSIS", "notebook", "", "x = 1", [], "", "claude-opus-5-5")
    assert s.status == "signin" and "Sign in again" in s.notes and "temporary" not in s.notes


def test_project_background_goes_first(monkeypatch, tmp_path):
    from wishbridge.warehouses.ssis.config import load_config

    (tmp_path / "output" / "inventory").mkdir(parents=True)
    (tmp_path / "output" / "inventory" / "inventory.csv").write_text(
        "schema_name,table_name,column_name,data_type,is_nullable\ndw,FactDailySales,OrderDate,date,NO\n"
        "dw,DimProduct,ProductKey,int,NO\n", encoding="utf-8")
    (tmp_path / "project.yml").write_text("source: ssis\ndatabricks: {catalog: workspace, schema: salesdw}\n",
                                          encoding="utf-8")
    ctx = ai.project_context(load_config(tmp_path / "project.yml"), "INSERT INTO dw.FactDailySales ...")
    assert "`workspace.salesdw`" in ctx and "dw.FactDailySales (OrderDate date NOT NULL)" in ctx
    assert "DimProduct" not in ctx  # only the tables this file uses
    seen = _claude_code(monkeypatch, {"is_error": False, "result": "NOTES:\n- nothing"})
    ai.ask_claude("SSIS", "notebook", "", "x = 1", [], "", "m", context=ctx)
    assert seen["input"].index("<project_context>") < seen["input"].index("<original>")


def test_project_switch_is_off_until_turned_on(tmp_path):
    from wishbridge.warehouses.ssis.config import load_config

    (tmp_path / "project.yml").write_text("source: ssis\n", encoding="utf-8")
    assert load_config(tmp_path / "project.yml").assist_enabled is False
    (tmp_path / "project.yml").write_text("source: ssis\nautofix: {assist: true}\n", encoding="utf-8")
    assert load_config(tmp_path / "project.yml").assist_enabled is True


class _FakeWorkspace:
    """Stands in for databricks.sdk.WorkspaceClient: two served models, one answer."""
    asked = {}

    def __init__(self, profile=None, config=None, **kw):
        from types import SimpleNamespace as N

        profile = profile or getattr(config, "profile", None)  # long requests pass their settings in a Config
        self.profile = profile

        def query(name, messages, max_tokens):
            _FakeWorkspace.asked = {"profile": profile, "name": name, "messages": messages}
            return N(choices=[N(message=N(content="```python\nx = 2\n```\nNOTES:\n- fixed"), finish_reason="stop")])

        eps = [N(name="databricks-claude-sonnet-4", config=N(served_entities=[])),
               N(name="databricks-meta-llama-3-3-70b-instruct", config=N(served_entities=[]))]
        self.serving_endpoints = N(list=lambda: eps, query=query)


def test_databricks_lists_claude_models_and_asks_with_the_project_login(monkeypatch):
    import databricks.sdk

    import databricks.sdk.config
    from types import SimpleNamespace

    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", _FakeWorkspace)
    monkeypatch.setattr(databricks.sdk.config, "Config", lambda **kw: SimpleNamespace(**kw))  # no real sign-in
    assert ai.databricks_claude_endpoints("DEFAULT") == ["databricks-claude-sonnet-4"]
    s = ai.ask_claude("SSIS", "notebook", "<DTS/>", "x = 1", [], "", "claude-opus-5-5",
                      via="databricks", profile="DEFAULT", endpoint="databricks-claude-sonnet-4")
    assert s.status == "ok" and s.sql == "x = 2\n"
    assert _FakeWorkspace.asked["profile"] == "DEFAULT" and _FakeWorkspace.asked["name"] == "databricks-claude-sonnet-4"
    assert "<DTS/>" in _FakeWorkspace.asked["messages"][1].content


def test_databricks_needs_a_chosen_model():
    assert ai.assist_backend("databricks", "DEFAULT", "")[0] is None
    assert ai.assist_backend("databricks", "", "x")[0] is None


def test_claude_code_status_reads_auth_status(monkeypatch):
    monkeypatch.setattr(ai.shutil, "which", lambda name: "/usr/local/bin/claude")
    out = {"stdout": '{"loggedIn": false, "authMethod": "none"}'}
    monkeypatch.setattr(ai.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out["stdout"], ""))
    ok, msg = ai.claude_code_status()
    assert not ok and "not signed in" in msg
    out["stdout"] = '{"loggedIn": true, "email": "dev@example.com"}'
    assert ai.claude_code_status() == (True, "Claude Code is signed in (dev@example.com).")


def test_edits_are_applied_to_the_current_file():
    current = "a = 1\nb = 2   \nc = 3\n"
    answer = ("<<<<<<< SEARCH\nb = 2\n=======\nb = 20\n>>>>>>> REPLACE\n"
              "<<<<<<< SEARCH\nc = 3\n=======\nc = 3\nd = 4\n>>>>>>> REPLACE\nNOTES:\n- b fixed\n- d added")
    s = ai.apply_edits(current, answer)
    assert s.status == "ok" and s.sql == "a = 1\nb = 20\nc = 3\nd = 4\n" and s.notes.startswith("- b fixed")


def test_an_edit_that_does_not_match_fails_clearly():
    s = ai.apply_edits("a = 1\n", "<<<<<<< SEARCH\nzzz\n=======\ny\n>>>>>>> REPLACE")
    assert s.status == "failed" and "does not match" in s.notes
    assert ai.apply_edits("a = 1\n", "NOTES:\n- nothing to change").sql == "a = 1\n"


def test_401_means_sign_in_again(monkeypatch):
    _claude_code(monkeypatch, {"is_error": True, "result": "Failed to authenticate. API Error: 401 Invalid access token."},
                 signed_in=True)
    s = ai.ask_claude("SSIS", "notebook", "", "x = 1", [], "", "claude-sonnet-5-5")
    assert s.status == "signin" and "Sign in again" in s.notes and "401" in s.notes


def test_connection_test_reports_quickly(monkeypatch):
    _claude_code(monkeypatch, {"is_error": False, "result": "OK"})
    ok, msg = ai.test_claude_code("claude-sonnet-5-5")
    assert ok and msg.startswith("Claude Code answered in")


def test_a_hanging_claude_code_starts_the_sign_in_repair(monkeypatch):
    _claude_code(monkeypatch, {"is_error": False, "result": "OK"})
    monkeypatch.setattr(ai, "_CLAUDE_CODE_OK", {})
    monkeypatch.setattr(ai, "test_claude_code", lambda model, seconds=60: (False, "Claude did not answer within 25 s"))
    s = ai.ask_claude("SSIS", "notebook", "", "x = 1", [], "", "claude-sonnet-5-5")
    assert s.status == "signin" and "quick check" in s.notes


def test_claude_code_ignores_personal_gateway_settings(monkeypatch):
    # seen on a real computer: ~/.claude/settings.json sent every request to a company gateway with a dead token
    seen = _claude_code(monkeypatch, {"is_error": False, "result": "NOTES:\n- ok"})
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Token: secret")
    monkeypatch.setenv("CLAUDE_CODE_USE_GATEWAY", "1")
    ai.ask_claude("SSIS", "notebook", "", "x = 1", [], "", "claude-sonnet-5-5")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--setting-sources") + 1] == "project,local"
    assert not {"ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_USE_GATEWAY"} & set(seen["env"])


def test_a_question_gets_an_answer_and_optional_changes(monkeypatch):
    seen = _claude_code(monkeypatch, {"is_error": False, "result": "It loads daily sales.\n"
                                      "<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE"})
    monkeypatch.setattr(ai, "_CLAUDE_CODE_OK", {"m": 9e12})
    s = ai.ask_question("What does it do? Also set x to 2", "SSIS", "notebook", "<DTS/>", "x = 1\n", [],
                        "- a.py [review]: 1 open issues", "", "m", history=[("earlier q", "earlier a")], file="a.py")
    assert s.status == "ok" and s.notes == "It loads daily sales." and s.sql == "x = 2\n"
    assert "earlier q" in seen["input"] and "<project_files>" in seen["input"]
    _claude_code(monkeypatch, {"is_error": False, "result": "Only a.py uses it."})
    monkeypatch.setattr(ai, "_CLAUDE_CODE_OK", {"m": 9e12})
    assert ai.ask_question("Which files?", "SSIS", "notebook", "", "x = 1\n", [], "", "", "m").sql is None
