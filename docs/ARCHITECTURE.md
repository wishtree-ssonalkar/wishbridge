# WishBridge architecture - for developers

How WishBridge is built, where things live, and how to change or debug it. For using it, see
[USER_GUIDE.md](USER_GUIDE.md); for the Databricks App, [DATABRICKS_APP.md](DATABRICKS_APP.md).

## The big picture

```
Entry points ─ Laptop:          Start WishBridge.cmd → wishbridge ui (Streamlit)   ·   wishbridge … (command line)
             └ Databricks App:  databricks_app/app_main.py → the same Streamlit app

1. Shared shell           src/wishbridge/                         (the same for every warehouse)
2. Warehouse packages     src/wishbridge/warehouses/<warehouse>/   (one independent copy per warehouse)
3. The project folder     C:\WishBridge\…\<project>  (laptop)  ·  a zip on a Unity Catalog volume (app)
```

WishBridge builds on the Databricks Labs migration toolkit (LakeBridge): the toolkit analyzes and converts the
code; WishBridge adds the fit check, fix rules, Claude, deployment, data copy, comparison, reports and the screens.

## 1. Shared shell - `src/wishbridge/`

| File | What it does |
|---|---|
| `ui/app.py` | The app's frame: system check (laptop) or app check (Databricks), first page, sidebar (open, create, upload a project), then hands the open project to its warehouse (`ui_for(source).run(raw)`). |
| `ui/helpers.py` | Project folders: create, read/save `project.yml`, unpack an uploaded code zip. |
| `hosting.py` | **The only place that knows laptop vs Databricks App**: which Databricks client (saved login or the app's identity), where projects are kept (volume, saved after every change), the app check, sharing the schemas the app creates. Everything else asks `hosting.is_app()` / `hosting.workspace_client()`. |
| `toolkit_setup.py` | Installs the migration toolkit inside the Databricks App (from PyPI, in the background). |
| `open_package.py` | Turns an assessment zip back into a project (rewrites stored paths for the new place). |
| `discover.py` | Looks at a folder of client code: which source system, how many databases. |
| `config.py`, `sources/` | The list of source systems (SQL Server, SSIS, Teradata…) and what each needs. |
| `system.py` | The laptop's system check and installer (CLI, Java, toolkit, converters). |
| `cli.py` | The `wishbridge` command; forwards to the project's warehouse. |
| `warehouses/__init__.py` | `project.yml` `source:` → the warehouse package that handles it (`WAREHOUSE_OF`). |

## 2. Warehouse packages - `src/wishbridge/warehouses/<warehouse>/`

Each warehouse (bigquery, datastage, informatica, netezza, oracle, redshift, snowflake, sqlserver, **ssis**,
synapse, teradata) is a **complete, independent copy**, so it can analyze, convert and copy data its own way and a
change for one warehouse cannot break another. The files are the same in every copy:

**Pipeline steps** - each a module with one main function; its result is saved in `output/state.json`:

| Step | Module | Result |
|---|---|---|
| Describe | `fit.py`, `overview.py` | Is it a data warehouse? What is in it? |
| Analyze | `analysis.py` → `lakebridge.analyze` | Files, complexity, effort |
| Convert | `convert.py` (+ `staging.py`, `rules.py`) → `lakebridge.transpile` | Converted code, findings, automatic fixes |
| Deploy | `deploy.py` (+ `schema.py`, `dbcode.py`) | Tables, procedures, notebooks in the test schema |
| Copy the data | `data.py` + one module per method: `source_db.py` (federation), `exported_files.py`, `localdb.py` (this computer) | Rows copied per table |
| Compare | `reconcile.py` | Row counts, sums, checksums: source vs Databricks |
| Report, zips | `report.py`, `package.py` | `report*.html`, the zips in `output/review_package/` |

**Services** used by the steps:

| Module | What it talks to |
|---|---|
| `dbx.py` | A Databricks SQL warehouse (`Warehouse(cfg).run(sql)`) |
| `lakebridge.py` | The migration toolkit (offline: a stand-in workspace on localhost, see the file) |
| `ai.py` | Claude (Claude Code, the workspace's model, or the API) - code only, never data |
| `inventory.py` | The client's database, read-only (assessment) |
| `state.py`, `config.py` | `output/state.json`; `project.yml` → `ProjectConfig` |
| `runner.py` | Runs steps in a **background process** (`python -m wishbridge pipeline …`), writes `output/logs/run_status.json` (the page shows progress from it) and `output/logs/pipeline.log` |

**Screens** - `ui/main.py` is the step bar; `ui/steps/<tab>.py` is one tab each (`settings`, `code`, `run`,
`fix`, `data`, `report`, `share`); `ui/claude_panel.py` is *Ask Claude about this problem*.

## 3. The project folder - the data

```
<project>/
  project.yml            all settings (no passwords - ever)
  input/                 the client's code as received
  database_code/         tables and procedures read from the database
  overrides/             hand fixes (Fix code) - used on every later run, never overwritten
  output/state.json      every step's result (the screens read this)
  output/final/          converted code
  output/logs/           pipeline.log, run_status.json, toolkit_<step>.log
  output/review_package/ the zips
```

On the laptop it is a folder; in the Databricks App the whole folder is kept as one zip on the projects volume and
unpacked into the app's working folder when opened (`hosting.push` / `hosting.pull`).

## Laptop and Databricks App

The same code and screens. `hosting.is_app()` is true inside Databricks Apps (`DATABRICKS_APP_NAME`). A choice that
cannot work where you are shows **"On the laptop only"** and what to use instead (`hosting.LAPTOP_ONLY`) - it is
never hidden. Differences live in `hosting.py` and in a few clearly marked `if hosting.is_app():` branches in the
screens. The app runs **Python 3.11** (no 3.12-only syntax - `tests/test_python311.py`).

## Changing WishBridge

**A fix for every warehouse** (most fixes):
1. Change the file in `ssis/` (the reference copy).
2. `python tools/sync_warehouses.py <file>` copies it to the other warehouses (their package name filled in),
   skipping every warehouse that customized that file.
3. Run the tests.

**A change for one warehouse only** (it works differently):
1. Change the file in that warehouse's folder.
2. Add the file to that warehouse's `CUSTOMIZED.txt` with one line on why, e.g.
   `analysis.py - Teradata BTEQ scripts are analyzed per statement`.
3. From then on the sync tool leaves that file alone and the drift test does not compare it.

`tests/test_warehouse_drift.py` fails when a shared file differs from `ssis/` - e.g. a fix made in one warehouse
and forgotten in the others - and says how to fix it. `python tools/sync_warehouses.py --check` shows the same.

## Debugging - where to look

| Symptom | Look at |
|---|---|
| A run stopped or a step failed | The screen's *What the run said last*; `output/logs/pipeline.log`; the step's entry in `output/state.json` |
| Analyze or convert failed | `output/logs/toolkit_analyze.log`, `toolkit_transpile.log` (the toolkit's full output and exit code) |
| A SQL statement failed in Databricks | The Deployment / Data report (statement and Databricks' message), `output/data/load_plan.sql` |
| The Databricks App misbehaves | `databricks apps logs wishbridge --profile <login>`; the app check in the sidebar |
| Run one step by hand | `wishbridge pipeline -c <project>/project.yml --steps analyze --options "{}"` |

## Tests and deployment

- `pytest` (316+ tests) runs without Databricks, the toolkit or a database - Databricks and the toolkit are
  replaced by stand-ins. UI tests use Streamlit's `AppTest`.
- Run the tests before every deployment. `deploy-databricks-app.ps1` also refuses code that does not compile.
- Commit to `Shubham/WishbridgePhase1`; merge to `main` with a pull request.
