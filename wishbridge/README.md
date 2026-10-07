# Wishtree WishBridge

**End-to-end migration accelerator from legacy data warehouses and ETL to Databricks, by Wishtree Technologies.**
Built on [Databricks Labs LakeBridge](https://github.com/databrickslabs/lakebridge).

LakeBridge gives you an analyzer, transpilers and a reconciler. WishBridge turns them into a
repeatable migration pipeline and fills the gaps between them:

| Step | Command | Powered by | What WishBridge adds |
|---|---|---|---|
| 1. Assess | `wishbridge analyze` | LakeBridge Analyzer | Parsed results, effort estimate |
| 2. Convert | `wishbridge convert` | LakeBridge transpilers (Morph / BladeBridge) | Rule engine that auto-fixes leftovers and flags blockers with line numbers; optional AI fix suggestions (Claude) |
| 3. Deploy & validate | `wishbridge deploy` | Databricks SQL warehouse | Creates converted objects in a dev schema; EXPLAINs every query/DML; per-statement pass/fail |
| 4. Load data | `wishbridge load` | Lakehouse Federation or `COPY INTO` | Load plan + execution with row counts |
| 5. Reconcile | `wishbridge reconcile` | Databricks SQL / LakeBridge Reconcile | Quick count + numeric checksum checks; `--full` runs LakeBridge reconcile |
| 6. Report | `wishbridge report` | — | Client-ready HTML report across all steps |

```
 legacy code ──► analyze ──► convert ──► deploy ──► load ──► reconcile ──► report.html
                (LakeBridge)  (LakeBridge     (SQL        (Federation /   (checks)
                               + rules + AI)   warehouse)   COPY INTO)
```

## Supported sources

`wishbridge sources` lists them: SQL Server, Azure Synapse, Snowflake, Oracle, Teradata, Redshift,
BigQuery, Netezza (SQL) and DataStage, Informatica PowerCenter / Cloud, SSIS (ETL, via BladeBridge).
The rule engine currently has the deepest coverage for SQL Server / Synapse.

## Install

Prerequisites: Python 3.10+, Java 11+, the [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install), and a workspace login.

```powershell
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile DEFAULT
databricks labs install lakebridge
databricks labs lakebridge install-transpile --interactive false

pip install -e ".[ai]"          # from this folder; drop [ai] if you don't need Claude suggestions
```

## Quick start

```powershell
cd examples\mssql-demo
wishbridge run                    # analyze + convert + report (local only, nothing touches Databricks)
start output\report.html
```

With a SQL warehouse available:

```powershell
wishbridge deploy                 # create objects in main.wishbridge_demo and validate every statement
wishbridge load                   # write the data load plan (output\data\load_plan.sql)
wishbridge load --execute         # run it
wishbridge reconcile              # compare source vs target
wishbridge report
```

New project:

```powershell
wishbridge init acme-dw --source mssql
# copy the legacy .sql files into acme-dw\input, edit acme-dw\project.yml
cd acme-dw; wishbridge run
```

## Project file

See [examples/mssql-demo/project.yml](examples/mssql-demo/project.yml). Key settings:

| Setting | Meaning |
|---|---|
| `source` | Source system key (`wishbridge sources`) |
| `databricks.profile` / `warehouse_id` | CLI profile and SQL warehouse (blank = first running warehouse) |
| `databricks.catalog` / `schema` | **Dev** schema that `deploy` creates objects in |
| `schema_map` | Rename source schemas in converted code, e.g. `dbo: main.sales` |
| `autofix.ai` | Ask Claude for fix suggestions on files with open issues (needs `ANTHROPIC_API_KEY`) |
| `data.method` | `federation` (INSERT from a foreign catalog) or `files` (COPY INTO from a volume) |
| `data.tables` | Tables to load and reconcile; target derived from `schema_map` unless given |

## Output

Everything goes into the project's `output` folder:

```
output/
  analysis/analysis.xlsx      LakeBridge analyzer workbook
  converted/                  raw transpiler output
  final/                      converted code after WishBridge rules  <- the deliverable
  ai_suggestions/             Claude suggestions + notes (only with --ai; never applied automatically)
  logs/transpile_errors.log
  data/load_plan.sql
  state.json                  machine-readable results of every step
  report.html
```

## Safety

- `deploy` runs DDL in the configured dev schema and only **EXPLAINs** DML. `--execute-dml` runs it.
- `load` writes a plan by default; `--execute` is required to move data.
- Targets whose name contains a `prod` / `production` segment are refused unless `--allow-prod` is passed.
- AI suggestions are written to a separate folder for human review; converted code is never overwritten by them.
  With `--ai`, source code is sent to the Claude API. Only enable it where the client allows that.

## Rule engine

Fixes (safe, mechanical rewrites; strings and comments are never touched):
schema mapping, `[bracket]` identifiers, `NOLOCK` hints, `GETDATE()` / `GETUTCDATE()`, `SET NOCOUNT`, `GO`,
comments that split column aliases.

Flags (need a human): `@@` system variables, `@` local variables, `#temp` tables, leftover `TOP`,
`OUTPUT INSERTED`, cursors, dynamic SQL, `RAISERROR`, transactions, `IDENTITY_INSERT`, leftover `CONVERT`,
and every `FIXME` / unsupported marker left by the transpiler.

Add rules in [src/wishbridge/rules.py](src/wishbridge/rules.py). Each rule is one line plus a test in
[tests/test_rules.py](tests/test_rules.py).

## Development

```powershell
pip install -e ".[dev,ai]"
pytest
```

## Licence notes

WishBridge calls LakeBridge as an external CLI and does not redistribute it. LakeBridge is licensed under the
Databricks License, which permits use only in connection with Databricks services; WishBridge is intended for
migrations to Databricks and is used the same way.
