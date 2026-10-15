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


def _claude_code(monkeypatch, answer: dict):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ai.shutil, "which", lambda name: "/usr/local/bin/claude")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(answer) + "\n", "")

    monkeypatch.setattr(ai.subprocess, "run", fake_run)
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
    _claude_code(monkeypatch, {"is_error": True, "result": "Failed to authenticate: OAuth session expired"})
    s = ai.ask_claude("SSIS", "notebook", "", "x = 1", [], "", "claude-opus-5-5")
    assert s.status == "failed" and "claude auth login" in s.notes


def test_project_switch_is_off_until_turned_on(tmp_path):
    from wishbridge.warehouses.ssis.config import load_config

    (tmp_path / "project.yml").write_text("source: ssis\n", encoding="utf-8")
    assert load_config(tmp_path / "project.yml").assist_enabled is False
    (tmp_path / "project.yml").write_text("source: ssis\nautofix: {assist: true}\n", encoding="utf-8")
    assert load_config(tmp_path / "project.yml").assist_enabled is True
