# Wishtree WishBridge – User Guide

This guide takes you from a fresh laptop to a finished migration report. Follow the steps in order.
Commands are for **Windows PowerShell**; Mac/Linux differences are noted where they matter.

**What WishBridge does:** it migrates a **data warehouse** and the ETL that loads it to Databricks. You give
it the warehouse code (SQL Server, Oracle, Teradata, Snowflake, … or SSIS / Informatica / DataStage jobs). It
assesses the code, converts it to Databricks, fixes or flags what the converter got wrong, deploys it to a test
schema in Databricks, copies the data, checks the data matches, and writes an HTML report you can share with
the client.

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

---

## Part A – One-time setup (about 20 minutes)

### Quick setup (recommended)

Get the WishBridge folder (`git clone https://github.com/wishtree-ssonalkar/wishbridge.git C:\wishbridge`,
or download it as a ZIP from GitHub and unzip it), then:

| System | Run once | Then start WishBridge with |
|---|---|---|
| Windows 10/11 | Double-click **`Install WishBridge.cmd`** | The **Wishtree WishBridge** icon on the desktop |
| macOS | `./install.sh` in Terminal | **Wishtree WishBridge.command** on the Desktop |
| Linux (Ubuntu/Debian, RHEL/Fedora) | `./install.sh` in a terminal | **Wishtree WishBridge** in the app menu, or `./start-wishbridge.sh` |

The setup installs whatever is missing (Python, Java and the Databricks CLI, via winget on Windows, Homebrew on
macOS, apt or dnf on Linux), opens a browser to sign in to Databricks, installs LakeBridge and its converters,
installs WishBridge and adds the launcher. It is safe to run again; finished steps are skipped. Add
`-DryRun` (Windows) or `DRY_RUN=1` (macOS/Linux) to see what it would do without changing anything.

**Linux server without a desktop:** run `./start-wishbridge.sh --no-browser` on the server, open an SSH tunnel
from your laptop (`ssh -L 8501:localhost:8501 <server>`) and browse to http://localhost:8501. The app only
listens on its own machine unless started with `--host 0.0.0.0`; don't do that on an untrusted network, because
the app runs with that machine's Databricks login.

If the quick setup works, skip to [Part B](#part-b--try-the-demo-5-minutes-nothing-touches-databricks).
Steps A1–A4 below are the same setup done by hand.

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
.\.venv\Scripts\python.exe -m pip install -e ".[ai,ui]"
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

## The WishBridge app (no commands needed)

Everything in this guide can also be done in the WishBridge app, which opens in your browser.

- **Start it:** double-click `Start WishBridge.cmd` in the WishBridge folder (right-click → *Send to → Desktop*
  to make a desktop shortcut), or run `wishbridge ui`. It opens at http://localhost:8501 and runs only on
  your computer; close the black window to stop it.
- **Sidebar:** *Open a folder* takes either a WishBridge project or **any folder of client code** — a Git
  repository, a Visual Studio database project, an ETL export. For client code the app detects the source system
  (from `.sqlproj`, `.dtsx`, Informatica or DataStage XML, Informatica Cloud `.zip`, or the SQL keywords), finds
  separate databases inside it (e.g. TenantDB, AuditDB) and offers to create one project per database in
  `C:\migrations`. WishBridge keeps its own working copy of the code inside the project (with a fingerprint of every
  file, in `code_received.json`), so the client's folder is needed only once; the app does not show this. The
  client's folder is never written to.
  New projects start in the **assessment phase**. *Create new* makes an empty project to upload code into.
  Same from the command line: `wishbridge init acme --code C:\client-repo --dir C:\migrations`.
- **1 · Settings:** the **Phase** comes first and saves as soon as you change it.
  - *Assessment* (offline): only the source system and the converter (leave it on *Automatic*: it picks the right
    LakeBridge converter and retries files it can't convert with the other one). Schema names and AI / estimates
    are folded away. Nothing here needs Databricks.
  - *Migration*: also the **Databricks workspace** (pick the saved login for the client's workspace, or *Connect to
    another workspace* to sign in; *Load warehouses and catalogs* fills the lists), the test schema, what to
    deploy, the source database connection and the tables to copy. Press *Save settings* (bad values are refused
    and the old settings kept). Saving records the workspace URL in the project: from then on WishBridge refuses
    to deploy, load or reconcile with a login for any other workspace.
- **2 · Code:** the **code overview** (a document describing how the system is built), the **source database inventory** (download
  the read-only query for the DBA, import their CSV), upload more files and preview them.
- **3 · Run:** tick the steps (analyze, convert, deploy, copy data, reconcile, report) and press *Start*. In the
  assessment phase only analyze, convert and report can run (offline).
  Each step shows its result; a failed step stops the run and says why. Copying data only happens when
  *really copy* is ticked.
- **4 · Results:** the numbers, the fit check (*Is this a data warehouse?*), file statuses, open items,
  deployment errors, reconciliation and the full report, plus *Build the review package* (one zip with the report,
  open items, converted and original code).
- **5 · Fix code (later):** pick a file, see its open items and the original next to the Databricks version, edit it
  and press *Save as manual fix*. Then run Convert again.
- **System check (sidebar):** checks this computer when the app starts and installs **only what the work needs** -
  always the Databricks CLI and LakeBridge; for an open project also its converter(s) (Morph and/or BladeBridge) and,
  for Morph, Java 11+. Installs run **in the background** (winget on Windows, Homebrew on macOS, LakeBridge and the
  converters through the Databricks CLI) while you keep working. Versions are checked against the minimum
  (e.g. Java 11). If something cannot be installed - for example an office network blocks the download - nothing
  is blocked: steps that need it stop with a message, everything else works, and every check and install attempt is
  saved to `~/.wishbridge/system_check.json` (and the project's `output/logs/`, so it travels in the zip).
  *Install missing again* retries. Same from the command line: `wishbridge setup` (`-c project.yml` for a project).

The app and the commands use the same project folders, so you can switch between them at any time.

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

### How an engagement runs: two visits

Clients rarely let us onto their systems again and again, so WishBridge collects everything in **one first
visit** and does the rest at Wishtree.

**Visit 1 – collect (one time, at the client)**

1. Open the client's code folder in the app (or `wishbridge init <name> --code <folder>`). WishBridge keeps
   a working copy of the code in the project (with a fingerprint of every file in `code_received.json`), so their
   folder is never needed again. The project starts in the **assessment phase**.
2. Give the client's DBA the **inventory query** (app: *Code > Source database inventory*, or
   `wishbridge inventory`). It only reads the database catalog: tables, columns, data types, row counts, sizes.
   Import the CSV they send back (`wishbridge inventory --import file.csv`). No connection to their database.
3. Press **Run > Assess and save everything** (or `wishbridge assess` then `wishbridge package`). On this computer
   only - nothing goes to Databricks or the client's database, and no Databricks login is needed - it analyzes,
   converts, writes the report and the **code overview** (`code_overview.html`: structure, every table with its
   columns, every procedure with the tables it reads and writes, data flows, what needs attention, old and new
   files) and saves **everything in one zip**: `original_code/`, `converted_code/`, `code_overview.html`, `report.html`, `open_items.csv`, `files.csv`, `fit_check.csv`, `inventory_tables.csv`, the analyzer workbook
   and the project itself. **Nothing has to be fixed at the client.**

**At Wishtree – offline**

4. Open the zip on any computer: app sidebar > **Open a package**, or `wishbridge open-package <zip> --dir C:\migrations`.
   It becomes a working project again (paths are moved to the new place).
5. Fix the files that need it in **Fix code (later)**: the open items say what to change and on which line, the
   original code is shown next to the Databricks version, and *Save as manual fix* keeps your version in
   `overrides/` for every later run. Run Convert again and build a new package when done.

**Visit 2 – migrate**

5. Switch the project to the **migration phase** (Settings > Phase, or `phase: migration`). Then deploy, copy the
   data from the dev database once, run the ETL and reconcile (steps C6–C8). The inventory's row counts are the
   baseline.

While a project is in the assessment phase WishBridge refuses every step that would reach Databricks or the
client's database.


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

### C4a. Check it really is a data warehouse (fit check)

WishBridge migrates data warehouses, not the databases behind live applications. Clients do not always say
which one they are handing over, so **Analyze** also runs a fit check (or run it on its own in seconds:
`wishbridge fit --details`). Every object gets a recommendation with its reasons:

| Recommendation | Typical objects | What to do |
|---|---|---|
| **Move to Databricks** | Report procedures, aggregations, ETL / load / merge procedures, views, their helper functions | Convert and deploy |
| **Copy the data** | Tables, seed-data scripts | Create on Databricks and copy the data (step C7) |
| **Keep on source** | Insert/update/delete procedures, paged searches for screens, lookups by id, login / e-mail, triggers | Leave with the application |
| **Not needed** | Users, roles, indexes, partition functions, SSDT deployment scripts, framework tables (`__EFMigrationsHistory`) | Nothing |
| **Decide** | No clear signals | Decide by hand |

It also gives a verdict for the whole database:

- **Good fit**: a data warehouse / ETL code base. Migrate it all.
- **Application database**: not a warehouse. Do not migrate it; if the warehouse needs its data, add it as a
  new source (Lakehouse Federation, Lakeflow Connect or CDC).
- **Mixed**: migrate the warehouse part (`scope: recommended`), leave the application part.

To deploy only what belongs on Databricks, set `scope: recommended` in `project.yml` (or **What to deploy**
in the app's Settings). The fit check uses names and code patterns, so read the reasons before you rely on it.

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

Code alone can't copy data: WishBridge needs to know **where the old database is and how to log in**.
Copying data is optional — skip this step and WishBridge still converts and deploys the code.

**Connect the source database (recommended).** In the app (migration phase): *Settings → Source database*. Choose the database
type, enter the server, port, database (or Oracle service name) and a read-only user and password, then press
**Create connection**. WishBridge stores the password in Databricks secrets (never in `project.yml` or on your
computer), creates a Lakehouse Federation connection and a catalog `wb_<project>_source` that shows the old
database inside Databricks, and fills in the data-copy settings. Press **Test connection** — Databricks reads the
list of schemas — then pick a schema, **List tables**, tick the tables and **Add** them to the copy list.

From the command line, put the details in `project.yml` and run `wishbridge connect` (it asks for the password,
or reads `WISHBRIDGE_SOURCE_PASSWORD`):

```yaml
source_db: {type: sqlserver, host: sqlprod01.client.com, port: 1433, database: SalesDB, user: migration_reader}
```

Types: `sqlserver`, `sqldw` (Synapse), `oracle` (`database` = service name), `snowflake` (also
`options: {sfWarehouse: ETL_WH}`), `teradata`, `redshift`, `postgresql`, `mysql`.
`wishbridge connect --test` re-tests, `--remove` deletes the connection, catalog and stored password.

Creating a connection needs the Databricks *CREATE CONNECTION* privilege. **Databricks must be able to reach the
database over the network**: a cloud database usually needs an allow-list entry, an on-premises one a VPN or
private link — ask IT early. *Test connection* tells you within 3 minutes if it can't.

**No network route? Use files.** Export each table to Parquet/CSV, upload into a Unity Catalog volume with one
folder per table, and set *Method* to `files` and *Files root* to `/Volumes/<catalog>/<schema>/<volume>`. This is
also the route for BigQuery and Netezza, which Lakehouse Federation can't reach with a user and password.

Then list the tables (or pick them in the app as above):

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

**ETL tools (Informatica, DataStage, SSIS).** These convert to Databricks notebooks (PySpark) instead of SQL.
`wishbridge deploy` uploads them to your workspace folder (`/Workspace/Users/<you>/wishbridge/<project>`), and

```powershell
wishbridge execute            # runs the converted notebooks as one Databricks job
```

**SSIS master packages.** LakeBridge drops Execute Package tasks, so a master package converts to an empty
notebook. WishBridge reads the order from the packages (Execute Package tasks and precedence constraints) and
runs the notebooks as **one Databricks job whose tasks depend on each other**, e.g. `LoadDimCustomer -> LoadFactSales`.
If a task fails, the tasks after it are skipped, as in SSIS. The job definition is written to
`output\jobs\<project>.json`; create the scheduled job from it with `databricks jobs create --json @output\jobs\<project>.json`.

WishBridge also checks the Spark SQL inside the notebooks. It fixes SSIS expression leftovers (`+` between
strings, `"x"` literals that became backtick identifiers, `FINDSTRING`, `LEN`, `REPLACENULL`), applies `schema_map`,
and flags what needs a person: empty column lists (often an Aggregate), `(DT_...)` casts, SSIS functions, and BIT
flags compared with 1/0 (BIT arrives in Databricks as BOOLEAN). See `examples\ssis-demo`.

runs them after the input tables are loaded. To prove the migrated job produces the same result as the old one,
list the job's output table with `load: false` and its legacy output as the source:

```yaml
  tables:
    - dbo.Orders                                                          # input: copied
    - {source: legacy_out.ORDERS_CLEAN, target: main.sales.ORDERS_CLEAN, load: false}   # output: compared only
```

`load` never copies a `load: false` table (in `overwrite` mode it empties it so a re-run starts clean), and
`reconcile` compares it with the legacy output. ETL exports contain no table definitions, so put the
`CREATE TABLE` statements for the job's tables in `overrides\` as an extra file (e.g. `00_tables.sql`).

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
| `wishbridge assess` | Offline first visit: analyze + convert + report |
| `wishbridge inventory [--import FILE]` | Read-only inventory query for the DBA / import their CSV |
| `wishbridge package` | Review package zip (report, open items, converted + original code) |
| `wishbridge snapshot` | Copy the client's code into the project (for projects that read it in place) |
| `wishbridge run` | Assess + convert + report |
| `wishbridge run --deploy --load` | The whole pipeline |
| `wishbridge analyze` | Assess the legacy code (includes the fit check) |
| `wishbridge fit [--details]` | What belongs on Databricks vs stays with the application |
| `wishbridge convert [--ai]` | Convert and check |
| `wishbridge deploy [--recreate]` | Create objects in the test schema |
| `wishbridge load [--execute]` | Plan / copy the data |
| `wishbridge execute` | Run converted ETL notebooks as a Databricks job |
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
