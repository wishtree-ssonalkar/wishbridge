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
    click.echo(f"  2. cd {root} && wishbridge run")


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
    _ok(f"{s['files']} files: {s['ready']} ready, {s['review']} to review, {s['needs_fix']} need fixes; "
        f"{s['auto_fixed']} issues auto-fixed")
    for f in res["files"]:
        colour = {"ready": "green", "review": "yellow", "needs-fix": "red"}[f["status"]]
        click.echo(f"  {click.style(f'{f["status"]:<9}', fg=colour)} {f['file']}")
        for x in f["findings"]:
            if not x["fixed"] and x["severity"] != "info":
                click.echo(f"      line {x['line']}: {x['message']}")
    click.echo(f"  Output: {res['final_dir']}")


@main.command()
@config_option
@click.option("--execute-dml", is_flag=True, help="Also execute INSERT/UPDATE/DELETE/MERGE (default: EXPLAIN only).")
@click.option("--allow-prod", is_flag=True, help="Allow a target schema whose name contains 'prod'.")
def deploy(config_path: str, execute_dml: bool, allow_prod: bool) -> None:
    """Create converted objects in the dev schema and validate every statement."""
    from .deploy import run_deploy

    cfg = _load(config_path)
    click.echo(f"Deploying to {cfg.target_schema} ...")
    res = _guard(run_deploy, cfg, execute_dml, allow_prod)
    s = res["summary"]
    _ok(f"{s['statements_ok']}/{s['statements']} statements OK, {s['files_ok']}/{s['files']} files clean")
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
        ctx.invoke(deploy, config_path=config_path, execute_dml=False, allow_prod=False)
    if with_load:
        ctx.invoke(load, config_path=config_path, execute=True, allow_prod=False)
        ctx.invoke(reconcile, config_path=config_path, full=False)
    ctx.invoke(report, config_path=config_path)


if __name__ == "__main__":
    main()
