"""System check / automatic install, and the code overview document."""

from pathlib import Path

from wishbridge import system
from wishbridge.config import load_config
from wishbridge.overview import build_overview, parse_tables


def test_missing_requirements_are_installed_in_order(monkeypatch):
    present: set[str] = set()
    ran: list[str] = []
    reqs = [
        system.Requirement("cli", "CLI", "", lambda: "x" if "cli" in present else None, lambda: [["install-cli"]], "m"),
        system.Requirement("lb", "LakeBridge", "", lambda: "x" if "lb" in present else None, lambda: [["install-lb"]], "m",
                           after=("cli",)),
        system.Requirement("java", "Java", "", lambda: None, lambda: [], "install java by hand"),
    ]

    def fake_run(cmd, timeout=0):
        ran.append(cmd[0])
        present.add({"install-cli": "cli", "install-lb": "lb"}[cmd[0]])
        return True, "done"

    monkeypatch.setattr(system, "requirements", lambda: reqs)
    monkeypatch.setattr(system, "_run", fake_run)
    monkeypatch.setattr(system, "refresh_path", lambda: None)
    statuses = {s.key: s for s in system.install_missing()}
    assert ran == ["install-cli", "install-lb"]  # LakeBridge only after the CLI
    assert statuses["cli"].ok and statuses["lb"].ok
    assert not statuses["java"].ok and "by hand" in statuses["java"].log[0]


def test_network_failures_get_a_hint(monkeypatch):
    req = system.Requirement("cli", "CLI", "", lambda: None, lambda: [["x"]], "m")
    monkeypatch.setattr(system, "requirements", lambda: [req])
    monkeypatch.setattr(system, "_run", lambda cmd, timeout=0: (False, "x509: certificate signed by unknown authority"))
    monkeypatch.setattr(system, "refresh_path", lambda: None)
    (s,) = system.install_missing()
    assert not s.ok and system.NETWORK_HINT in s.log


def test_tables_are_read_from_ddl_without_comments():
    ddl = """CREATE TABLE [dbo].[Orders] (
        [OrderID] INT IDENTITY(1,1) NOT NULL,  -- the key, with a comma, inside a comment
        [Status] NVARCHAR(20) NULL DEFAULT ('New, open'),
        [Amount] DECIMAL(12,2) NOT NULL,
        CONSTRAINT [PK_Orders] PRIMARY KEY CLUSTERED ([OrderID] ASC)
    );"""
    (t,) = parse_tables(ddl)
    assert t["name"] == "dbo.Orders" and t["primary_key"] == ["OrderID"]
    assert [(c["name"], c["type"]) for c in t["columns"]] == [("OrderID", "INT"), ("Status", "NVARCHAR(20)"),
                                                              ("Amount", "DECIMAL(12,2)")]
    assert t["columns"][0]["identity"] and not t["columns"][0]["nullable"]


def test_overview_describes_the_code_base(tmp_path):
    code = tmp_path / "input"
    (code / "Tables").mkdir(parents=True)
    (code / "Procs").mkdir()
    (code / "Tables" / "Orders.sql").write_text("CREATE TABLE dbo.Orders (OrderID INT NOT NULL PRIMARY KEY, Amount DECIMAL(12,2));",
                                                encoding="utf-8")
    (code / "Tables" / "DailySales.sql").write_text("CREATE TABLE dbo.DailySales (Day DATE, Total DECIMAL(18,2));", encoding="utf-8")
    (code / "Procs" / "LoadDaily.sql").write_text(
        "CREATE PROCEDURE dbo.usp_load_daily_sales AS BEGIN TRUNCATE TABLE dbo.DailySales; "
        "INSERT INTO dbo.DailySales SELECT CAST(GETDATE() AS DATE), SUM(Amount) FROM dbo.Orders; "
        "DECLARE c CURSOR FOR SELECT 1; EXEC sp_executesql @sql; END", encoding="utf-8")
    (tmp_path / "project.yml").write_text("source: mssql\n", encoding="utf-8")
    cfg = load_config(tmp_path / "project.yml")
    out = build_overview(cfg)
    html = out.read_text(encoding="utf-8")
    assert "2 tables, 1 stored procedures" in html
    assert "dbo.usp_load_daily_sales" in html and "Cursors" in html and "Dynamic SQL" in html
    assert "Old and new files" in html and "Procs/LoadDaily.sql" in html
    from wishbridge.state import load_state

    ov = load_state(cfg)["overview"]
    assert ov["tables"] == 2 and ov["procedures"] == 1 and ov["features"]["cursor"] == 1
    # who loads what: the procedure writes DailySales and reads Orders
    i = html.index("How data flows")
    assert "dbo.DailySales" in html[i:] and "dbo.usp_load_daily_sales" in html[i:]
    assert not Path(cfg.output_dir / "code_overview.html").read_text(encoding="utf-8").count("copied")
