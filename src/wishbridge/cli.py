"""Wishtree WishBridge command line."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from .config import SOURCES, ConfigError, load_config, render_template
from .dbx import SqlError
from .lakebridge import LakeBridgeError

config_option = click.option("-c", "--config", "config_path", default="project.yml", show_default=True,
                             type=click.Path(dir_okay=False), help="Project file.")


def _fail(msg: str) -> None:
    click.secho(f"[error] {msg}", fg="red", err=True)
    sys.exit(1)


def _ok(msg: str) -> None:
    click.secho(f"[ok] {msg}", fg="green")


def _load(path: str):
    try:
        return load_config(path)
    except ConfigError as e:
        _fail(str(e))


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (LakeBridgeError, SqlError, ConfigError, FileNotFoundError, RuntimeError) as e:
        _fail(str(e))


@click.group()
@click.version_option(__version__, prog_name="wishbridge")
def main() -> None:
    """Wishtree WishBridge - migrate legacy SQL and ETL to Databricks.

    Pipeline: analyze -> convert -> deploy -> load -> reconcile -> report
    """


@main.command()
def sources() -> None:
    """List supported source systems."""
    for s in SOURCES.values():
        click.echo(f"  {s.key:<18} {s.analyzer_tech:<20} transpiler={s.transpiler}")


@main.command()
@click.argument("name")
@click.option("--source", "source", required=True, type=click.Choice(list(SOURCES)), help="Source system.")
@click.option("--dir", "directory", default=".", type=click.Path(file_okay=False), help="Parent directory.")
def init(name: str, source: str, directory: str) -> None:
    """Create a new migration project folder."""
    root = Path(directory) / name
    if (root / "project.yml").exists():
        _fail(f"{root / 'project.yml'} already exists")
    (root / "input").mkdir(parents=True, exist_ok=True)
    (root / "project.yml").write_text(render_template(name, source), encoding="utf-8")
    _ok(f"Created {root}")
    click.echo(f"  1. Copy your {SOURCES[source].analyzer_tech} files into {root / 'input'}")
    click.echo(f"  2. Review {root / 'project.yml'} (catalog, schema, schema_map)")
    click.echo(f"  3. cd {root}")
    click.echo("     wishbridge run")
    click.echo("  Step-by-step guide: docs/USER_GUIDE.md")


@main.command()
@config_option
def analyze(config_path: str) -> None:
    """Assess the legacy code (LakeBridge Analyzer)."""
    from .analysis import run_analyze

    cfg = _load(config_path)
    click.echo(f"Analyzing {cfg.input_dir} as {cfg.source.analyzer_tech} ...")
    res = _guard(run_analyze, cfg)
    cx = ", ".join(f"{k}={v}" for k, v in sorted(res["complexity"].items()))
    _ok(f"{len(res['programs'])} files ({cx}); manual estimate {res['estimated_hours_baseline']} h")
    click.echo(f"  Report: {res['report_file']}")


@main.command()
@config_option
@click.option("--ai/--no-ai", default=None, help="Ask Claude for fix suggestions (overrides project.yml).")
def convert(config_path: str, ai: bool | None) -> None:
    """Transpile to Databricks SQL and apply WishBridge fix rules."""
    from .convert import run_convert

    cfg = _load(config_path)
    click.echo(f"Converting with {cfg.transpiler} ({cfg.source.dialect}) ...")
    res = _guard(run_convert, cfg, ai)
    s = res["summary"]
    manual = f"; {s['manual_overrides']} manual fixes applied" if s.get("manual_overrides") else ""
    _ok(f"{s['files']} files: {s['ready']} ready, {s['review']} to review, {s['needs_fix']} need fixes; "
        f"{s['auto_fixed']} issues auto-fixed{manual}")
    for f in res["files"]:
        colour = {"ready": "green", "review": "yellow", "needs-fix": "red"}[f["status"]]
        tag = "  (manual fix)" if f.get("manual_override") else ""
        click.echo(f"  {click.style(f'{f["status"]:<9}', fg=colour)} {f['file']}{tag}")
        for x in f["findings"]:
            if not x["fixed"] and x["severity"] != "info":
                click.echo(f"      line {x['line']}: {x['message']}")
    click.echo(f"  Output: {res['final_dir']}")


@main.command()
@config_option
@click.option("--execute-dml", is_flag=True, help="Also execute INSERT/UPDATE/DELETE/MERGE (default: EXPLAIN only).")
@click.option("--allow-prod", is_flag=True, help="Allow a target schema whose name contains 'prod'.")
@click.option("--recreate", is_flag=True, help="Rebuild existing tables/views/procedures (CREATE OR REPLACE).")
def deploy(config_path: str, execute_dml: bool, allow_prod: bool, recreate: bool) -> None:
    """Create converted objects in the dev schema and validate every statement."""
    from .deploy import run_deploy

    cfg = _load(config_path)
    click.echo(f"Deploying to {cfg.target_schema} ...")
    res = _guard(run_deploy, cfg, execute_dml, allow_prod, recreate)
    s = res["summary"]
    kept = f" ({s['kept_existing']} existing objects kept)" if s.get("kept_existing") else ""
    _ok(f"{s['statements_ok']}/{s['statements']} statements OK, {s['files_ok']}/{s['files']} files clean{kept}")
    for f in res["files"]:
        for x in f["results"]:
            if not x["ok"]:
                click.secho(f"  {f['file']} #{x['n']}: {x['error']}", fg="red")


@main.command()
@config_option
@click.option("--execute", is_flag=True, help="Run the load (default: write the plan only).")
@click.option("--allow-prod", is_flag=True, help="Allow targets whose name contains 'prod'.")
def load(config_path: str, execute: bool, allow_prod: bool) -> None:
    """Copy table data into Databricks (federation or files)."""
    from .data import run_load

    cfg = _load(config_path)
    res = _guard(run_load, cfg, execute, allow_prod)
    s = res["summary"]
    if execute:
        _ok(f"{s['loaded']}/{s['tables']} tables loaded, {s['failed']} failed")
        for t in res["tables"]:
            if t["status"] == "failed":
                click.secho(f"  {t['target']}: {t['error']}", fg="red")
    else:
        _ok(f"Plan for {s['tables']} tables written to {res['plan_file']} (re-run with --execute)")


@main.command()
@config_option
@click.option("--full", is_flag=True, help="Use LakeBridge reconcile (row/column level) instead of quick checks.")
def reconcile(config_path: str, full: bool) -> None:
    """Compare source and target data."""
    from .reconcile import run_reconcile

    cfg = _load(config_path)
    res = _guard(run_reconcile, cfg, full)
    if res["mode"] == "quick":
        s = res["summary"]
        _ok(f"{s['matched']}/{s['tables']} tables match ({s['mismatched']} mismatched, {s['errors']} errors)")
    else:
        _ok("LakeBridge reconcile finished - see the reconcile dashboard in Databricks")


@main.command()
@config_option
def report(config_path: str) -> None:
    """Build the HTML migration report."""
    from .report import build_report

    cfg = _load(config_path)
    _ok(f"Report: {_guard(build_report, cfg)}")


@main.command()
@click.option("--project", "project", default=None, type=click.Path(file_okay=False), help="Project folder to open.")
@click.option("--port", default=8501, show_default=True, help="Local port for the app.")
def ui(project: str | None, port: int) -> None:
    """Open the WishBridge app in your browser."""
    import subprocess

    try:
        import streamlit  # noqa: F401
    except ImportError:
        _fail('The UI needs Streamlit: pip install -e ".[ui]"')
    app = Path(__file__).parent / "ui" / "app.py"
    args = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(port),
            "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal", "--"]
    if project:
        args += ["--project", str(Path(project).resolve())]
    elif Path("project.yml").exists():
        args += ["--project", str(Path.cwd())]
    click.echo(f"WishBridge app: http://localhost:{port}  (Ctrl+C to stop)")
    subprocess.run(args)


@main.command("sql")
@config_option
@click.argument("sql_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--allow-prod", is_flag=True, help="Allow statements that name a 'prod' catalog/schema.")
def sql_cmd(config_path: str, sql_file: str, allow_prod: bool) -> None:
    """Run a SQL file on the project's SQL warehouse, statement by statement."""
    from .config import looks_like_prod
    from .dbx import Warehouse
    from .sqltext import mask, split_statements

    cfg = _load(config_path)
    stmts = split_statements(Path(sql_file).read_text(encoding="utf-8-sig"))
    if not allow_prod and any(looks_like_prod(w) for s in stmts for w in mask(s).split()):
        _fail("The file references a 'prod' object. Re-run with --allow-prod if that is intended.")
    wh = _guard(Warehouse, cfg)
    for i, stmt in enumerate(stmts, 1):
        res = _guard(wh.run, stmt)
        first = " ".join(mask(stmt).split())[:70]
        click.echo(f"  #{i} ok  {first}" + (f"  -> {res.rows[0]}" if res.rows and len(res.rows) == 1 else ""))
    _ok(f"{len(stmts)} statements run on warehouse {wh.warehouse_id}")


@main.command()
@config_option
@click.option("--ai/--no-ai", default=None, help="Ask Claude for fix suggestions.")
@click.option("--deploy", "with_deploy", is_flag=True, help="Also deploy & validate on Databricks.")
@click.option("--load", "with_load", is_flag=True, help="Also load data (executes) and reconcile.")
@click.pass_context
def run(ctx: click.Context, config_path: str, ai: bool | None, with_deploy: bool, with_load: bool) -> None:
    """Run the pipeline: analyze, convert, report (+ deploy, load, reconcile when asked)."""
    ctx.invoke(analyze, config_path=config_path)
    ctx.invoke(convert, config_path=config_path, ai=ai)
    if with_deploy or with_load:
        ctx.invoke(deploy, config_path=config_path, execute_dml=False, allow_prod=False, recreate=False)
    if with_load:
        ctx.invoke(load, config_path=config_path, execute=True, allow_prod=False)
        ctx.invoke(reconcile, config_path=config_path, full=False)
    ctx.invoke(report, config_path=config_path)


if __name__ == "__main__":
    main()
