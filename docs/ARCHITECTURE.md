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

## Warehouse by warehouse

Each warehouse folder can process its code and data its own way. This section shows what already differs, where a
warehouse's own logic goes, and what each is expected to need when it is tested on its own.

### What each warehouse does differently today

Converter: the first one tried in automatic mode, then the second on files with errors. The Databricks App installs Java 21
and both converters, so it converts the same way. "DBA query": the client's DBA runs a read-only query and sends its CSV.

| Folder | Source(s) | Kind | Converter (laptop) | In the app | Tables read from | Data copy | Own rules (sources/&lt;warehouse&gt;.py) |
|---|---|---|---|---|---|---|---|
| `sqlserver` | SQL Server (`mssql`) | Warehouse DB | Morpheus → BladeBridge | Same | Live (ODBC, Azure SQL too) + procedures/views (`CODE_QUERY`) | Federation `sqlserver`, files, this computer | 6 fixes (brackets, NOLOCK, GETDATE, SET NOCOUNT, GO…), 13 checks, T-SQL tidy before converting (`PREP`) |
| `synapse` | Azure Synapse | Warehouse DB | Morpheus → BladeBridge | Same | Live (Entra ID sign-in) | Federation `sqldw`, files | 1 type mapping |
| `snowflake` | Snowflake | Warehouse DB | Morpheus only | Same | DBA query | Federation `snowflake`, files | 1 fix (max VARCHAR), 3 checks, 2 types |
| `oracle` | Oracle | Warehouse DB | Morpheus → BladeBridge | Same | DBA query | Federation `oracle`, files | 8 checks, 2 types |
| `teradata` | Teradata | Warehouse DB | BladeBridge → Morpheus | Same | DBA query (type codes) | Federation `teradata`, files | 5 checks, type codes |
| `redshift` | Amazon Redshift | Warehouse DB | Morpheus → BladeBridge | Same | DBA query | Federation `redshift`, files | - |
| `bigquery` | Google BigQuery | Warehouse DB | Morpheus only | Same | DBA query | Exported files only (no federation) | 5 types |
| `netezza` | IBM Netezza | Warehouse DB | BladeBridge only | Same | DBA query | Exported files only (no federation) | 3 checks |
| `ssis` | SSIS | ETL (loads a warehouse) | BladeBridge → Spark SQL | Same | The warehouse DB beside the packages (`companion.py`) | As its warehouse DB | Master package → Databricks job (`orchestration.py`), own screen (`ui/screens/ssis.py`) |
| `informatica` | PowerCenter, Informatica Cloud | ETL | BladeBridge → Spark SQL (Cloud: PySpark) | Same | As SSIS | As its warehouse DB | Cloud .zip export check (`staging.py`), ETL screen |
| `datastage` | IBM DataStage | ETL | BladeBridge → Spark SQL | Same | As SSIS | As its warehouse DB | .dsx/.isx export check (`staging.py`), ETL screen |

### Customization points - where a warehouse's own logic goes

Each stage is one file with a few main functions. To make a warehouse work differently, change these in its folder
and list the file in its `CUSTOMIZED.txt`.

| Stage | File · functions | What typically differs per warehouse |
|---|---|---|
| Knowledge about the source | `sources/<warehouse>.py` - `SOURCES`, `INVENTORY_QUERY`, `TYPES`, `FEDERATION`, `FIXES`, `DETECTORS`, `PREP`, `CODE_QUERY` | Dialect, converter, table-list query, type map, federation type, rewrite rules and checks. **Start here - most differences fit in this file.** |
| Recognise the code | `discover.py` · `inspect()`; `HINTS` in `sources/<warehouse>.py` | File types and keywords that identify the source |
| Prepare the code | `staging.py` · `source_files()`, `prepare_tsql()`, `check_export_format()` | Which files are code, clean-ups before converting, export formats (DataStage, Informatica Cloud) |
| Fit check | `fit.py` · `run_fit()` | What counts as warehouse logic vs application logic |
| Analyze | `analysis.py` · `run_analyze()`, `estimate_hours()` | Analyzer source type, effort per file or complexity |
| Convert | `convert.py` · `run_convert()`, `choose_best_converter()`; `lakebridge.py` · `transpile()` | Converter order, converter options (target technology for ETL), per-file choices |
| Fix rules | `rules.py` · `apply_rules()`, `detect()` (shared) + `FIXES`/`DETECTORS` in `sources/<warehouse>.py` | Mechanical rewrites and checks for the dialect |
| ETL jobs | `orchestration.py` · `job_tasks()`; `companion.py` | How packages, workflows or job sequences become a Databricks job |
| Read the client's database | `inventory.py` · `read_live()`, `script()`; `dbcode.py` · `read()` | Driver, sign-in, queries for tables and code |
| Deploy | `deploy.py` · `run_deploy()`; `schema.py` · `scripts()` | Table DDL, types, objects Databricks does not have (sequences, synonyms) |
| Copy the data | `data.py` · `build_plan()`, `run_load()`; `source_db.py` (federation), `exported_files.py`, `localdb.py` | Connection type, export format, casts, incremental loads |
| Compare | `reconcile.py` · `compare_table()` | Which types count as equal (NUMBER vs DECIMAL, timestamps, strings) |
| Screens | `ui/screens/<tool>.py`, `ui/steps/code.py` | What the person sees and fills in for this source |

### Planned per-warehouse work

Only SSIS has been tested end to end. When each warehouse gets its own sample project and test, these are the
differences we expect to build - each in that warehouse's folder, listed in its `CUSTOMIZED.txt`:

| Folder | Expected differences | Files likely to change |
|---|---|---|
| `sqlserver` | More T-SQL patterns (temp tables, cursors, MERGE options, IDENTITY), procedure-to-notebook choices | `sources/sqlserver.py`, `rules.py`, `staging.py`, `deploy.py` |
| `synapse` | Distribution / index options removed, CTAS, PolyBase external tables | `sources/synapse.py`, `rules.py`, `schema.py` |
| `snowflake` | VARIANT and semi-structured types, streams and tasks as jobs, conversion on the laptop only | `sources/snowflake.py`, `schema.py`, `orchestration.py`, `reconcile.py` |
| `oracle` | PL/SQL packages split per procedure, sequences and synonyms, NUMBER precision | `staging.py`, `deploy.py`, `schema.py`, `reconcile.py` |
| `teradata` | BTEQ and FastLoad scripts, type codes, primary index removed, TPT exports | `staging.py`, `analysis.py`, `sources/teradata.py`, `exported_files.py` |
| `redshift` | DISTKEY/SORTKEY removed, UNLOAD to cloud storage for the data | `sources/redshift.py`, `rules.py`, `exported_files.py` |
| `bigquery` | STRUCT/ARRAY types, exports from Cloud Storage, conversion on the laptop only | `schema.py`, `exported_files.py`, `reconcile.py` |
| `netezza` | DISTRIBUTE ON / ORGANIZE ON removed, nzsql exports | `sources/netezza.py`, `rules.py`, `exported_files.py` |
| `ssis` | Script tasks, event handlers, package parameters | `orchestration.py`, `convert.py`, `ui/screens/ssis.py` |
| `informatica` | Workflows and sessions as jobs, Cloud mappings (PySpark) | `orchestration.py`, `staging.py`, `ui/screens/etl.py` |
| `datastage` | Job sequences as jobs, parallel-job stages | `orchestration.py`, `staging.py`, `ui/screens/etl.py` |

This list is a plan, not a record: it changes as each warehouse is tested. What a warehouse actually changed is
always in its `CUSTOMIZED.txt`.

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

## Install, deploy and use

Same code and screens on both; pick per client and per phase. Usual flow: **1** assessment on the laptop at the
client (offline) → assessment zip → **2** code migration in the app in Wishtree's workspace (or on the laptop) →
**3** once the client agrees, the app in the client's workspace: deploy, copy the data, compare, sign-off.
Everything can also be done on the laptop alone, signed in to the client's workspace.

### Laptop (desktop)

1. **Install once** (about 20 minutes): `git clone https://github.com/wishtree-ssonalkar/wishbridge.git C:\wishbridge`
   (or the ZIP), then double-click `Install WishBridge.cmd` - Python, Java, Databricks CLI, Databricks sign-in, the
   migration toolkit and converters, WishBridge and a desktop icon. Safe to run again; `-DryRun` shows what it would
   do. macOS / Linux: `./install.sh`.
2. **Start**: the *Wishtree WishBridge* desktop icon (or `Start WishBridge.cmd`) → http://localhost:8501 (this
   computer only). Close the window to stop it.
3. **Use**: *Check this computer* → Assessment, Code migration or Data migration → open a folder, upload the code,
   open the assessment zip or a project → the tabs. Projects: `C:\WishBridge\Assessments`, `C:\WishBridge\Migrations`.
4. **Update**: `cd C:\wishbridge`, `git pull`, restart (installed from this folder; if new packages are needed, run
   the installer again). Expired sign-in: `databricks auth login --profile DEFAULT`.

### Databricks App (deployed on Databricks)

1. **Before**: on a laptop with WishBridge's folder and the Databricks CLI - a login to the workspace
   (`databricks auth login --profile CLIENT`), the right to create apps, a SQL warehouse ID, a catalog for the
   projects volume and the catalog(s) for test schemas.
2. **Deploy** (about 5 minutes):
   `.\deploy-databricks-app.ps1 -Profile CLIENT -WarehouseId <id> -ProjectsVolume /Volumes/<catalog>/wishbridge/projects -TargetCatalogs <catalog> -ShareWith <team-group>`
   - volume, app, permissions, compile check, upload, start; prints the link.
   **Or from Git**: Compute › Apps › wishbridge › Deploy › from Git, repository `wishtree-ssonalkar/wishbridge`,
   the branch with the app. Databricks reads `app.yaml` and `requirements.txt` at the repository root. In a new
   workspace run the script once (permissions, `sql-warehouse` resource).
3. **Give access**: Compute › Apps › wishbridge › Permissions → CAN USE. People sign in with their Databricks account.
4. **Use**: the same screens; the sidebar's App check shows the workspace, warehouse, project storage and the
   migration toolkit (installed by itself about a minute after start). Projects are saved in the workspace.
5. **Update**: run the same deploy command (only changed files are uploaded; projects stay). Run `pytest` first.
6. **Day to day**: logs `databricks apps logs wishbridge --profile CLIENT`; a running app costs compute -
   `databricks apps stop wishbridge` / `start wishbridge`; remove with `databricks apps delete wishbridge` (projects stay).

| Which one when? | Laptop | Databricks App |
|---|---|---|
| Assessment at the client, offline | best | upload the code; processed in the workspace |
| Code and data migration | signed in to the client's workspace | best when the client wants everything in their workspace, or several people work on it |
| A database on this computer (demo) | yes | on the laptop only |
| Snowflake / BigQuery conversion (Morpheus) | yes | yes (Java 21 installed in the app) |
| Claude | Claude Code, workspace model, API key | workspace model, API key |

## Tests and deployment

- `pytest` (316+ tests) runs without Databricks, the toolkit or a database - Databricks and the toolkit are
  replaced by stand-ins. UI tests use Streamlit's `AppTest`.
- Run the tests before every deployment. `deploy-databricks-app.ps1` also refuses code that does not compile.
- Commit to `Shubham/WishbridgePhase1`; merge to `main` with a pull request.
