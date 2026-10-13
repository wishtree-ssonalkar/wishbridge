"""Wishtree WishBridge command line."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from .config import SOURCES, ConfigError, load_config, render_template

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
    except (ConfigError, FileNotFoundError, RuntimeError, ValueError, OSError) as e:
        _fail(str(e))


@click.group()
@click.version_option(__version__, prog_name="wishbridge")
def main() -> None:
    """Wishtree WishBridge - migrate legacy SQL and ETL to Databricks.

    Pipeline: analyze -> convert -> deploy -> load -> reconcile -> report
    """


@main.command()
def sources() -> None:
    """List supported source systems (data warehouses and the ETL tools that load them)."""
    from .config import PURPOSE, SCOPE_ROWS

    click.echo(PURPOSE)
    for s in SOURCES.values():
        click.echo(f"  {s.key:<18} {s.label:<32} transpiler={s.transpiler}")
    click.echo("What to migrate:")
    for what, answer in SCOPE_ROWS:
        click.echo(f"  - {what}: {answer}")


@main.command()
@click.argument("name")
@click.option("--source", "source", default=None, type=click.Choice(list(SOURCES)),
              help="Source system (detected from --code when omitted).")
@click.option("--dir", "directory", default=".", type=click.Path(file_okay=False), help="Parent directory.")
@click.option("--code", "code", default=None, type=click.Path(exists=True, file_okay=False),
              help="Client code folder (repository, database project, export).")
@click.option("--split/--no-split", default=True, show_default=True,
              help="With --code: one project per database when the code holds several.")
@click.option("--copy/--read-in-place", "copy_code", default=True, show_default=True,
              help="With --code: copy the code into the project with a receipt (so the client's folder is needed only "
                   "once), or read the client's folder in place.")
@click.option("--catalog", default="main", show_default=True, help="Databricks catalog for the test schema.")
def init(name: str, source: str | None, directory: str, code: str | None, split: bool, copy_code: bool,
         catalog: str) -> None:
    """Create a new migration project folder - or projects for an existing folder of client code."""
    if code:
        from . import discover

        info = _guard(discover.inspect, code)
        det = info.detection
        chosen = source or det.source
        click.echo(f"{info.files} code files in {info.path}")
        click.echo(f"Source system: {SOURCES[chosen].analyzer_tech}"
                   + ("" if source else f" (detected, {det.confidence} confidence: {det.reason})"))
        if not source and det.confidence == "low":
            _fail("Couldn't tell the source system for sure - re-run with --source <system>")
        if info.databases:
            click.echo("Databases: " + ", ".join(f"{n} ({c} files)" for n, _, c in info.databases))
        try:
            made = discover.create_projects_for_code(info, directory, name, chosen, split, catalog, copy_code=copy_code)
        except (ValueError, OSError) as e:
            _fail(str(e))
        for p in made:
            how = "code copied into" if copy_code else "reads"
            _ok(f"Created {p}  ({how} {discover.load_config(p / 'project.yml').input_dir}; phase: assessment, offline)")
        click.echo(f"  Next: cd {made[0]}  then  wishbridge assess")
        return
    if not source:
        _fail("Give --source <system>, or --code <folder> to detect it")
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
@click.option("--check-only", is_flag=True, help="Only report; do not install anything.")
@click.option("-c", "--config", "config_path", default=None,
              help="Project file: install only what this project needs (its converters, Java for Morph).")
@click.option("--need", default=None, help="Comma-separated requirement keys to install (used by the app).")
@click.option("--status-file", default=None, type=click.Path(dir_okay=False), help="Write progress and result here.")
@click.option("--also-save", multiple=True, type=click.Path(dir_okay=False), help="Also save the record here.")
def setup(check_only: bool, config_path: str | None, need: str | None, status_file: str | None,
          also_save: tuple[str, ...]) -> None:
    """Check this computer and install what the work needs; the record is saved to ~/.wishbridge/system_check.json."""
    from . import system

    needed = set(need.split(",")) if need else system.needed_for(_load(config_path) if config_path else None)
    extra = [Path(p) for p in also_save] + ([Path(status_file)] if status_file else [])

    def progress(msg: str) -> None:
        click.echo(f"  {msg}")
        system.save_report(system.report(system.check(needed), "running", msg), *extra)

    if not check_only:
        system.save_report(system.report(system.check(needed), "running", "Checking..."), *extra)
    statuses = system.check(needed) if check_only else system.install_missing(progress, needed)
    system.save_report(system.report(statuses), *extra)
    for s in statuses:
        if not s.needed and not s.ok:
            click.echo(f"  -    {s.name:<24} not needed for this work")
            continue
        good = s.ok and s.compatible
        tail = f" {s.version}" if s.version else ""
        click.secho(f"  {'ok  ' if good else 'MISS' if not s.ok else 'OLD '} {s.name:<24} {s.detail}{tail}",
                    fg="green" if good else "red")
        if s.ok and not s.compatible:
            click.echo(f"       version {s.version} is older than the minimum {s.minimum}")
        if not s.ok:
            click.echo(f"       by hand: {s.manual}")
            for line in s.log[-2:]:
                click.echo("       " + (line.strip().splitlines() or [""])[-1][:300])
    click.echo(f"  Record saved: {system.REPORT}")
    if system.ready(statuses):
        _ok("This computer is ready")
    else:
        _fail("Some requirements are missing (see above) - the record has the details")


@main.command("open-package")
@click.argument("zip_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--dir", "directory", default=".", type=click.Path(file_okay=False), help="Parent folder for the project.")
@click.option("--name", default=None, help="Project folder name (default: the name in the package).")
def open_package_cmd(zip_file: str, directory: str, name: str | None) -> None:
    """Recreate a project from a package, to review and fix the code on this computer."""
    from .open_package import open_package

    try:
        dest = open_package(zip_file, directory, name)
    except (ValueError, OSError, ConfigError) as e:
        _fail(str(e))
    _ok(f"Project ready: {dest}")
    click.echo(f"  Next: open it in the app (wishbridge ui --project {dest}), or cd {dest} and fix files in overrides/.")


@main.command()
@click.option("--project", "project", default=None, type=click.Path(file_okay=False), help="Project folder to open.")
@click.option("--port", default=8501, show_default=True, help="Local port for the app.")
@click.option("--host", default="localhost", show_default=True,
              help="Address to listen on. The default keeps the app private to this computer; "
                   "0.0.0.0 shares it on the network with everyone who can reach this machine.")
@click.option("--no-browser", is_flag=True, help="Don't open a browser (e.g. on a server).")
def ui(project: str | None, port: int, host: str, no_browser: bool) -> None:
    """Open the WishBridge app in your browser."""
    import subprocess

    try:
        import streamlit  # noqa: F401
    except ImportError:
        _fail('The UI needs Streamlit: pip install -e ".[ui]"')
    app = Path(__file__).parent / "ui" / "app.py"
    args = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(port),
            "--server.address", host, "--server.headless", str(no_browser).lower(),
            "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal", "--"]
    if project:
        args += ["--project", str(Path(project).resolve())]
    elif Path("project.yml").exists():
        args += ["--project", str(Path.cwd())]
    if host not in ("localhost", "127.0.0.1", "::1"):
        click.secho(f"Warning: the app is reachable from the network on {host}:{port} and runs with "
                    "this computer's Databricks login. Only do this on a trusted network.", fg="yellow")
    click.echo(f"WishBridge app: http://localhost:{port}  (Ctrl+C to stop)")
    subprocess.run(args)


# --- Project commands: run by the project's warehouse (src/wishbridge/warehouses/<warehouse>/cli.py) ----------

def _project_file(args: list[str]) -> str:
    for i, a in enumerate(args):
        if a in ("-c", "--config") and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--config="):
            return a.split("=", 1)[1]
    return "project.yml"


def _forward(name: str, help_text: str, hidden: bool = False) -> None:
    @main.command(name, help=help_text + " (run by the project's warehouse)", hidden=hidden, add_help_option=False,
                  context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
    @click.argument("args", nargs=-1, type=click.UNPROCESSED)
    def command(args: tuple[str, ...]) -> None:
        import yaml

        from .warehouses import module

        args = list(args)
        if "--help" in args or "-h" in args:
            source = "mssql"  # any warehouse shows the same options
        else:
            path = Path(_project_file(args))
            if not path.is_file():
                _fail(f"Project file not found: {path}. Pass -c <project.yml> or create one with `wishbridge init`.")
            source = str((yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}).get("source", ""))
        try:
            warehouse_cli = module(source, "cli")
        except ValueError as e:
            _fail(str(e))
        warehouse_cli.main.main(args=[name, *args], prog_name="wishbridge")


_forward("analyze", "Assess the legacy code (LakeBridge Analyzer).")

_forward("fit", "Check which objects belong on Databricks and which should stay with the application (fast, no LakeBridge).")

_forward("convert", "Transpile to Databricks SQL and apply WishBridge fix rules.")

_forward("deploy", "Create converted objects in the dev schema and validate every statement.")

_forward("load", "Copy table data into Databricks (federation or files).")

_forward("connect", "Connect the legacy source database to Databricks, so data can be copied.")

_forward("execute", "Run the converted ETL notebooks on Databricks (after deploy and load).")

_forward("reconcile", "Compare source and target data.")

_forward("pipeline", "Run steps and write progress to output/logs/run_status.json (used by the app, in the background).", hidden=True)

_forward("assess", "Offline first visit: analyze + convert + report. Nothing goes to Databricks or the client's database.")

_forward("snapshot", "Copy the client's code into the project (with a receipt), so their folder is not needed again.")

_forward("inventory", "Source database inventory (tables, columns, row counts, sizes) without connecting to the database.")

_forward("package", "Zip the review package: report, open items, fit check, inventory, converted (and original) code.")

_forward("report", "Build the HTML migration report.")

_forward("sql", "Run a SQL file on the project's SQL warehouse, statement by statement.")

_forward("run", "Run the pipeline: analyze, convert, report (+ deploy, load, reconcile when asked).")
