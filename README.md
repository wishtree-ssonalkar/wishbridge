# Wishtree WishBridge

**End-to-end migration accelerator from legacy data warehouses and ETL to Databricks, by Wishtree Technologies.**
Built on [Databricks Labs LakeBridge](https://github.com/databrickslabs/lakebridge).

**What WishBridge migrates:** data warehouses and the ETL that loads them.

| What the client has | Migrate with WishBridge? |
|---|---|
| SQL Server used as a data warehouse (fact/dimension tables, SSIS loads, Power BI or SSRS reports) | Yes |
| Oracle used as a data warehouse (often Exadata, PL/SQL loads, Informatica) | Yes |
| Teradata, Snowflake, Azure Synapse, Amazon Redshift, Google BigQuery, IBM Netezza | Yes |
| SSIS, Informatica or DataStage jobs that load a warehouse | Yes |
| SQL Server or Oracle behind a live application (order entry, bookings, HR screens) | No: it stays where it is |

SQL Server and Oracle are general database products, so the same software can hold a warehouse or an
application's data. Analyze runs a **fit check** that confirms the code is a warehouse and warns when it is not.

**New to WishBridge? Start with the step-by-step [User Guide](docs/USER_GUIDE.md).**
Prefer forms to commands? Double-click `Start WishBridge.cmd` (or run `wishbridge ui`) to open the WishBridge app
in your browser: create a project, fill in the settings, upload code, press Start and review the results.

LakeBridge gives you an analyzer, transpilers and a reconciler. WishBridge turns them into a
repeatable migration pipeline and fills the gaps between them:

| Step | Command | Powered by | What WishBridge adds |
|---|---|---|---|
| 1. Assess | `wishbridge analyze` | LakeBridge Analyzer | Parsed results, effort estimate, **migration fit check**: which objects belong on Databricks (reporting / ETL) and which stay with the application (CRUD, screens), with a verdict for the whole database |
| 2. Convert | `wishbridge convert` | LakeBridge transpilers (Morph / BladeBridge) | Rule engine that auto-fixes leftovers, catches mis-conversions the transpiler misses, flags blockers by line with Databricks guidance; keeps hand fixes across runs; optional AI fix suggestions (Claude) |
| 3. Deploy & validate | `wishbridge deploy` | Databricks SQL warehouse | Creates converted objects in a dev schema; EXPLAINs every query/DML; per-statement pass/fail; safe to re-run |
| 4. Load data | `wishbridge load` | Lakehouse Federation or `COPY INTO` | Load plan + execution with row counts |
| 5. Reconcile | `wishbridge reconcile` | Databricks SQL / LakeBridge Reconcile | Row counts, numeric sums and a whole-row checksum per table; `--full` runs LakeBridge reconcile |
| 6. Report | `wishbridge report` | — | Client-ready HTML report across all steps |

```
 legacy code ──► analyze ──► convert ──► deploy ──► load ──► reconcile ──► report.html
                (LakeBridge)  (LakeBridge     (SQL        (Federation /   (counts, sums,
                               + rules +       warehouse)   COPY INTO)      checksums)
                               overrides + AI)
```

## Tested end to end

The SQL Server example runs the whole pipeline on a real Databricks workspace (serverless SQL warehouse,
Unity Catalog): 4 files converted, 6/6 statements deployed, both migrated stored procedures executed with
`CALL`, 2 tables loaded and reconciled (counts, sums and checksums equal). Reconcile was also shown to catch
a single changed e-mail address that row counts and sums miss. 54 automated tests cover the rules, the
pipeline steps (against a stand-in warehouse) and the AI step (against a stand-in Claude client).

## Supported sources

`wishbridge sources` lists them: SQL Server, Azure Synapse, Snowflake, Oracle, Teradata, Redshift,
BigQuery, Netezza (SQL) and DataStage, Informatica PowerCenter / Cloud, SSIS (ETL, via BladeBridge).
Dialect rules exist for SQL Server / Synapse, Oracle, Snowflake, Teradata and Netezza; every source gets the
cross-dialect rules.

**The converter is chosen automatically.** WishBridge picks LakeBridge's better converter for the source system
(Morph for SQL Server, Synapse, Oracle, Snowflake, Redshift and BigQuery; BladeBridge for Teradata, Netezza and the
ETL tools). Where both converters support the source (SQL Server, Synapse, Oracle, Teradata, Redshift), any file
the first one leaves errors in is also run through the other, and the result with fewer errors is kept, file by file.
The report shows which converter produced each file. To force one converter, set `transpiler: morph` or
`transpiler: bladebridge` in `project.yml` (or pick it in the app).

## Install

**One-click setup:** Windows: double-click `Install WishBridge.cmd`. macOS / Linux: run `./install.sh`.
It installs anything missing (Python, Java, Databricks CLI), signs in to Databricks, installs LakeBridge and
WishBridge, and adds a desktop / app-menu launcher. Details: [User Guide, Part A](docs/USER_GUIDE.md).

By hand: Python 3.10+, Java 11+, the [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install), and a workspace login, then:

```powershell
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile DEFAULT
databricks labs install lakebridge
databricks labs lakebridge install-transpile --interactive false

pip install -e ".[ai,ui]"       # from this folder; [ai] = Claude suggestions, [ui] = the app
```

## Quick start (local only)

```powershell
cd examples\mssql-demo
wishbridge run                  # analyze + convert + report - nothing touches Databricks
start output\report.html
```

## Full demo on Databricks

The example uses catalog `workspace` (newer workspaces; older ones use `main` - edit `project.yml`).
It creates two schemas: `wishbridge_demo_src` (stand-in for the SQL Server source) and `wishbridge_demo` (target).

```powershell
cd examples\mssql-demo
wishbridge sql setup_demo_source.sql   # create the stand-in source tables with sample data
wishbridge run --deploy --load         # analyze, convert, deploy, load, reconcile, report
```

In a real project the source is a [Lakehouse Federation](https://docs.databricks.com/query-federation/) catalog
pointing at the legacy database, and `data.tables` lists the source tables (e.g. `- dbo.Customers`).

## New project

```powershell
wishbridge init acme-dw --source mssql
# copy the legacy .sql files into acme-dw\input, review acme-dw\project.yml
cd acme-dw; wishbridge run
```

## Fixing what the tools can't

1. `wishbridge convert` marks each file **ready**, **review** or **needs-fix** and lists open items with
   line numbers and the Databricks equivalent to use.
2. Copy the file from `output\final\` to `overrides\` (same name) and fix it there.
3. Re-run `wishbridge convert`: the hand-fixed file replaces the converted one on every run, is still checked
   by the rules, and shows as *manual fix* in the report. See `examples\mssql-demo\overrides\`.
4. `wishbridge deploy` proves it runs on Databricks.

Optionally `wishbridge convert --ai` (needs `ANTHROPIC_API_KEY`) writes Claude's suggested fix for each open
file to `output\ai_suggestions\` as a starting point for step 2.

## Commands

| Command | Purpose |
|---|---|
| `wishbridge init NAME --source X` | Create a project folder |
| `wishbridge init NAME --code DIR` | Project for a client folder: copies the code with a receipt; starts in the offline assessment phase |
| `wishbridge assess` | Offline first visit: analyze + convert + report (no Databricks, no client database) |
| `wishbridge inventory [--import FILE]` | Read-only inventory query for the client's DBA / import their CSV |
| `wishbridge package` | Everything in one zip: report, open items, fit check, inventory, original + converted code, the project |
| `wishbridge open-package ZIP --dir DIR` | Turn a package back into a working project on any computer |
| `wishbridge snapshot` | Copy the code of a project that reads the client's folder in place |
| `wishbridge sources` | List supported source systems |
| `wishbridge analyze` | LakeBridge assessment + effort estimate + fit check |
| `wishbridge fit [--details]` | Fit check only (seconds, no LakeBridge): move / copy data / keep on source / not needed |
| `wishbridge convert [--ai]` | Transpile, apply rules and overrides |
| `wishbridge deploy [--recreate] [--execute-dml]` | Create objects in the dev schema and validate (`--recreate` rebuilds existing objects) |
| `wishbridge load [--execute]` | Write (or run) the data load plan |
| `wishbridge reconcile [--full]` | Compare source and target |
| `wishbridge report` | Build `output\report.html` |
| `wishbridge run [--ai] [--deploy] [--load]` | Run the pipeline |
| `wishbridge sql FILE` | Run a SQL file on the project's warehouse |

All commands take `-c path\to\project.yml` (default: `project.yml` in the current folder).

## Project file

See [examples/mssql-demo/project.yml](examples/mssql-demo/project.yml). Key settings:

| Setting | Meaning |
|---|---|
| `source` / `transpiler` | Source system key (`wishbridge sources`); converter `auto` (default), `morph` or `bladebridge` |
| `input` / `output` / `overrides` | Legacy code, generated output, hand-fixed files |
| `scope` | `all` (default) or `recommended`: deploy only what the fit check says belongs on Databricks |
| `databricks.profile` / `warehouse_id` | CLI profile and SQL warehouse (blank = first running warehouse) |
| `databricks.catalog` / `schema` | **Dev** schema that `deploy` creates objects in |
| `schema_map` | Rename source schemas in converted code, e.g. `dbo: main.sales` |
| `autofix.ai` | Ask Claude for fix suggestions on files with open issues |
| `data.method` | `federation` (INSERT from a foreign catalog) or `files` (COPY INTO from a volume) |
| `data.tables` | Tables to load and reconcile; target derived from `schema_map` unless given |

## Output

```
output/
  analysis/analysis.xlsx      LakeBridge analyzer workbook
  converted/                  raw transpiler output
  final/                      converted code after rules and overrides  <- the deliverable
  ai_suggestions/             Claude suggestions + notes (only with --ai; never applied automatically)
  logs/transpile_errors.log
  data/load_plan.sql
  state.json                  machine-readable results of every step
  report.html
```

## Safety

- `deploy` runs DDL in the configured dev schema and only **EXPLAINs** DML. `--execute-dml` runs it.
  Objects that already exist are kept unless `--recreate` is given.
- `load` writes a plan by default; `--execute` is required to move data.
- Targets whose name contains a `prod` / `production` segment are refused unless `--allow-prod` is passed
  (`wishbridge sql` applies the same check).
- AI suggestions are written to a separate folder for human review; converted code is never overwritten by them.
  With `--ai`, source code is sent to the Claude API. Only enable it where the client allows that.

## Rule engine

**Fixes** (safe, mechanical rewrites; strings and comments are never touched):
schema mapping; T-SQL `[brackets]`, `NOLOCK`, `GETDATE()` / `GETUTCDATE()`, `SET NOCOUNT`, `GO`;
Snowflake `VARCHAR(16777216)` → `STRING`; comments that split column aliases; empty `CREATE /* ... */;`
shells left by the transpiler (commented out); missing `;` between statements that ran together.

**Flags** (need a human; each says what to use on Databricks):

| Source | Detected |
|---|---|
| All | `MERGE INTO <alias>` mis-conversions, `RAISE_ERROR` placeholders, cursors, dynamic SQL, transactions, every transpiler FIXME / parse error (untranslatable features get Databricks guidance: Change Data Feed, Jobs, volumes, IDENTITY, recursive CTEs, ...) |
| SQL Server / Synapse | `@@` system variables, `@` variables, `#temp` tables, `TOP`, `OUTPUT INSERTED`, `RAISERROR`, `IDENTITY_INSERT`, `CONVERT` |
| Oracle | `ROWNUM`, `CONNECT BY`, `(+)` joins, sequences / `NEXTVAL`, `DBMS_*` / `UTL_*`, `SQL%ROWCOUNT`, Oracle date masks |
| Snowflake | `@stage` references, `FLATTEN`, `TIMESTAMP_LTZ` / `TIMESTAMP_TZ` |
| Teradata | BTEQ dot-commands, `VOLATILE` / `MULTISET` tables, `PRIMARY INDEX`, `COLLECT STATISTICS`, `SEL`, `TOP` |

Add rules in [src/wishbridge/rules.py](src/wishbridge/rules.py), each with a test in `tests/`.

## Examples

| Folder | Shows |
|---|---|
| `examples/mssql-demo` | Full pipeline on Databricks, including overrides for the two files the transpiler gets wrong |
| `examples/snowflake-demo` | Streams, tasks, stages, VARIANT/FLATTEN |
| `examples/oracle-demo` | PL/SQL, sequences, CONNECT BY, `(+)` joins, ROWNUM |
| `examples/ssis-demo` | Visual Studio SSIS project: master package becomes one Databricks job with task dependencies; SSIS expressions fixed in the notebooks; hand fixes for an Aggregate and a BIT flag; output reconciled with the legacy load |

## Development

```powershell
pip install -e ".[dev,ai]"
pytest
```

## Licence notes

WishBridge calls LakeBridge as an external CLI and does not redistribute it. LakeBridge is licensed under the
Databricks License, which permits use only in connection with Databricks services; WishBridge is intended for
migrations to Databricks and is used the same way.
