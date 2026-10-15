"""'Ask Claude about this problem': only safe, known fixes reach the Apply list."""

from wishbridge.warehouses.ssis import ai


def test_only_additive_single_statements_are_allowed():
    assert ai.safe_sql("ALTER TABLE workspace.s.t ALTER COLUMN amount TYPE DECIMAL(18,4)")
    assert ai.safe_sql("CREATE VOLUME IF NOT EXISTS workspace.s.landing;")
    for bad in ["DROP TABLE workspace.s.t", "ALTER TABLE t DROP COLUMN x", "DELETE FROM t",
                "CREATE TABLE t (a INT)", "ALTER TABLE t ADD COLUMN a INT; DROP TABLE t", "TRUNCATE TABLE t",
                "CREATE OR REPLACE TABLE t AS SELECT 1", "GRANT SELECT ON t TO x"]:
        assert not ai.safe_sql(bad), bad


def test_actions_are_parsed_and_unknown_ones_dropped():
    text = ("The catalog main does not exist; use workspace.\n```json\n"
            '{"actions": [{"type": "setting", "key": "databricks.catalog", "value": "workspace", "why": "exists"},'
            ' {"type": "setting", "key": "autofix.assist", "value": "false"},'
            ' {"type": "sql", "sql": "DROP SCHEMA workspace.x"},'
            ' {"type": "column", "table": "w.s.t", "column": "amount", "source": "TotalAmount"},'
            ' {"type": "filter", "table": "w.s.t", "where": ""}]}\n```')
    answer, actions = ai.parse_actions(text)
    assert answer == "The catalog main does not exist; use workspace."
    assert [a["type"] for a in actions] == ["setting", "column", "filter"]
    assert actions[0]["value"] == "workspace"


def test_problem_request_carries_settings_and_problems(monkeypatch):
    seen = {}
    monkeypatch.setattr(ai, "assist_backend", lambda *a: ("api", "test"))
    monkeypatch.setattr(ai, "_send", lambda backend, system, user, *a, **k: (seen.update(system=system, user=user)
                                                                             or ("Use workspace.", None)))
    s = ai.ask_problem("Settings", ["Catalog 'main' does not exist"], "databricks: {catalog: main}", "", "", "", "m")
    assert s.status == "ok" and s.notes == "Use workspace." and s.actions == []
    assert "Catalog 'main' does not exist" in seen["user"] and "catalog: main" in seen["user"]
    assert "Never DROP" in seen["system"]
