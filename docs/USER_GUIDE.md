# Wishtree WishBridge – User Guide

This guide takes you from a fresh laptop to a finished migration report. Follow the steps in order.
Commands are for **Windows PowerShell**; Mac/Linux differences are noted where they matter.

**What WishBridge does:** you give it the SQL code of an old system (SQL Server, Snowflake, Oracle,
Teradata, …). It assesses the code, converts it to Databricks SQL, fixes or flags what the converter got
wrong, deploys it to a test schema in Databricks, copies the data, checks the data matches, and writes an
HTML report you can share with the client.

---

## Part A – One-time setup (about 20 minutes)

### A1. What you need

| Item | Check with | Get it from |
|---|---|---|
| Python 3.10 or newer | `python --version` | python.org (tick "Add to PATH") |
| Java 11 or newer | `java -version` | adoptium.net |
| Git | `git --version` | git-scm.com |
| Databricks CLI | `databricks --version` | `winget install Databricks.DatabricksCLI` |
| A Databricks workspace you can log in to, with a **SQL warehouse** | Databricks UI → SQL Warehouses | your Databricks admin |

> **Office network:** if any download fails with a *certificate* error (`x509`, `SSL`, `unknown authority`),
> the company firewall is inspecting traffic. Do the one-time setup on another network (e.g. a mobile hotspot)
> or ask IT to trust the firewall certificate. Day-to-day use works on the office network.

### A2. Log in to Databricks

```powershell
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile DEFAULT
```

A browser opens – sign in. Then confirm:

```powershell
databricks auth profiles        # the DEFAULT row should say Valid: YES
```

### A3. Install LakeBridge (Databricks' migration toolkit, which WishBridge uses)

```powershell
databricks labs install lakebridge
databricks labs lakebridge install-transpile --interactive false
```

Press Enter to accept the defaults if the installer asks questions.

### A4. Install WishBridge

```powershell
git clone https://github.com/wishtree-ssonalkar/wishbridge.git C:\wishbridge
cd C:\wishbridge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[ai]"
```

Check it works:

```powershell
.\.venv\Scripts\wishbridge.exe --version          # wishbridge, version 1.0.0
```

> **Tip – shorter commands:** run `.\.venv\Scripts\Activate.ps1` once per PowerShell window, then you can type
> just `wishbridge ...`. If PowerShell blocks the script, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or keep using the full path
> `C:\wishbridge\.venv\Scripts\wishbridge.exe`. The rest of this guide writes `wishbridge` for short.
> (Mac/Linux: `source .venv/bin/activate`.)

---

## Part B – Try the demo (5 minutes, nothing touches Databricks)

```powershell
cd C:\wishbridge\examples\mssql-demo
wishbridge run
start output\report.html
```

You should see 4 SQL Server files analysed and converted, and a report open in your browser.
This is the quickest way to show WishBridge to someone.

To run the **full** demo on Databricks (creates two demo schemas in the `workspace` catalog):

```powershell
wishbridge sql setup_demo_source.sql      # sample "source" tables
wishbridge run --deploy --load            # convert, deploy, load data, reconcile, report
```

If your workspace has no `workspace` catalog, open `project.yml` and replace `workspace` with `main`
(or another catalog you can create schemas in) everywhere.

---

## Part C – Migrate your own code

### C1. Create a project

Keep client projects outside the WishBridge folder, e.g. in `C:\migrations`:

```powershell
wishbridge init acme-dw --source mssql --dir C:\migrations
cd C:\migrations\acme-dw
```

`--source` is one of: `mssql`, `synapse`, `snowflake`, `oracle`, `teradata`, `redshift`, `bigquery`,
`netezza`, `datastage`, `informatica`, `informatica-cloud`, `ssis` (see `wishbridge sources`).

This creates:

```
acme-dw\
  project.yml     settings
  input\          put the legacy code here
```

### C2. Add the legacy code

Export the code from the old system (table definitions, views, stored procedures, report queries, ETL jobs)
as files and copy them into `acme-dw\input\`. Sub-folders are fine.

### C3. Edit `project.yml`

Open `acme-dw\project.yml` in any editor. Usually you only change these:

| Setting | Set it to |
|---|---|
| `databricks.catalog` | A catalog where you may create schemas (often `main` or `workspace`) |
| `databricks.schema` | A **test** schema name, e.g. `wishbridge_acme`. Never a production schema |
| `databricks.warehouse_id` | Leave blank to use the first running SQL warehouse, or paste a warehouse ID |
| `schema_map` | How old schema names map to new ones, e.g. `dbo: main.wishbridge_acme` |

Leave everything else as it is for now.

### C4. Assess and convert

```powershell
wishbridge run
start output\report.html
```

Every file gets a status:

| Status | Meaning | What to do |
|---|---|---|
| **ready** | Converted, nothing left to fix | Nothing |
| **review** | Converted, but has warnings | Read the warnings; often fine |
| **needs-fix** | Something will not run on Databricks | Fix it (step C5) |

The terminal and the report's **Open items** table list each problem with its line number and what to use
on Databricks instead. The converted code is in `output\final\`.

### C5. Fix what WishBridge could not

1. Copy the file from `output\final\` into a new folder `acme-dw\overrides\` (same file name).
2. Fix it there, using the open items as a checklist.
3. Run `wishbridge convert` again. Your fixed file now replaces the converted one on every run, is still
   checked, and shows as **manual fix** in the report.

Never edit files in `output\` – that folder is rebuilt on every run.

*Optional – AI suggestions:* with a Claude API key, Claude proposes a fix for each file with open items:

```powershell
$env:ANTHROPIC_API_KEY = "<your key>"
wishbridge convert --ai
```

Suggestions appear in `output\ai_suggestions\`. Review them, then copy what you accept into `overrides\`.
This sends the code to the Claude API – only use it where the client allows that.

### C6. Deploy to the test schema

```powershell
wishbridge deploy
```

Tables, views and procedures are created in your test schema. Queries and updates are only *checked*
(EXPLAIN), not run, so no data changes. Any statement that fails is listed – fix it in `overrides\` and
deploy again. Re-running is safe; add `--recreate` to rebuild objects that already exist.

### C7. Copy the data

First make the old database visible in Databricks. Two options:

- **Lakehouse Federation (recommended):** a Databricks admin creates a *connection* to the old database and a
  *foreign catalog* for it (Catalog Explorer → External data → Connections). In `project.yml` set
  `data.method: federation` and `data.source_catalog` to that catalog's name.
- **Files:** export each table to Parquet/CSV, upload into a Unity Catalog volume with one folder per table,
  and set `data.method: files`, `data.files_root: /Volumes/<catalog>/<schema>/<volume>`.

Then list the tables in `project.yml`:

```yaml
data:
  method: federation
  source_catalog: sqlserver_fed
  mode: overwrite            # or append
  tables:
    - dbo.Customers
    - dbo.Orders
```

Preview, then run:

```powershell
wishbridge load               # writes the plan to output\data\load_plan.sql - read it
wishbridge load --execute     # copies the data
```

### C8. Check the data matches

```powershell
wishbridge reconcile
```

For each table WishBridge compares the old and new copy: row count, totals of number columns, and a
checksum of every row. `match` means they are the same; `mismatch` shows which check differed.

### C9. Share the report

```powershell
wishbridge report
start output\report.html
```

`output\report.html` is a single file – email it or attach it to the project documentation.
It contains the assessment, conversion results, open items, deployment results, data load and
reconciliation.

---

## Part D – Command cheat sheet

| Command | What it does |
|---|---|
| `wishbridge init NAME --source X` | Create a project |
| `wishbridge run` | Assess + convert + report |
| `wishbridge run --deploy --load` | The whole pipeline |
| `wishbridge analyze` | Assess the legacy code |
| `wishbridge convert [--ai]` | Convert and check |
| `wishbridge deploy [--recreate]` | Create objects in the test schema |
| `wishbridge load [--execute]` | Plan / copy the data |
| `wishbridge reconcile` | Compare old vs new data |
| `wishbridge report` | Rebuild the HTML report |
| `wishbridge sql FILE` | Run a SQL file on the warehouse |
| `wishbridge sources` | List supported source systems |

Run commands from the project folder, or add `-c path\to\project.yml`.

---

## Part E – Troubleshooting

| Message | Fix |
|---|---|
| `certificate signed by unknown authority` / `SSL` | Company firewall – see the note in A1 |
| `DEFAULT and <name> match ... Use --profile` | Two CLI logins for the same workspace: delete the extra section from `C:\Users\<you>\.databrickscfg` |
| `cannot get access token` / `Valid: NO` | Log in again: `databricks auth login --profile DEFAULT` |
| `Transpiler 'morph' is not installed` | `databricks labs lakebridge install-transpile --interactive false` |
| `No SQL warehouses in this workspace` | Create a SQL warehouse, or set `databricks.warehouse_id` |
| `Catalog 'main' was not found` | Set `databricks.catalog` (and `schema_map`) to a catalog that exists, e.g. `workspace` |
| `looks like production` | WishBridge refuses `prod` schemas on purpose. Use a test schema |
| `already existed - kept` | Normal on re-runs. Use `wishbridge deploy --recreate` to rebuild |
| `AI fixes need Claude credentials` | Set `ANTHROPIC_API_KEY`, or run without `--ai` |
| `Project file not found` | Run the command inside the project folder, or pass `-c path\to\project.yml` |

---

## Good practice

- Always deploy to a **test** schema first; promote to production through your normal release process.
- Keep each project folder (including `overrides\`) in Git – it is the record of every manual fix.
- Don't commit `output\`; it is rebuilt on every run.
- Every manual fix you repeat across files is a candidate for a new WishBridge rule – tell the WishBridge team.
