# WishBridge local demo: SSIS + SQL Server

A complete client setup on this laptop, for testing every WishBridge phase end to end:
an order-entry system, a data warehouse with stored procedures, and the SSIS packages that load it.

```
DemoSalesOLTP (source system)  --SSIS packages-->  DemoSalesDW (warehouse: tables, procedures, views, function)
   Sales.Customer / Product /                         stg.Customer, dw.DimCustomer, DimProduct, DimDate,
   Orders / OrderLines                                FactDailySales (+ _Archive), SalesSummary, LoadLog
```

## Demo storyline (about 20 minutes)

| # | Show | Say |
|---|---|---|
| 1 | SSMS: `DemoSalesOLTP` | "The client's order system: 500 customers, 20,000 orders, ~50,000 order lines." |
| 2 | SSMS: `DemoSalesDW` + Visual Studio / the `.dtsx` files | "Every night SSIS loads it into the warehouse: customers merged into a dimension, sales summed per day per customer, old days archived, a monthly summary refreshed. The business rules live in the packages **and** in stored procedures." |
| 3 | Run `03_verify_sqlserver.sql` | "These are the numbers today. They must be identical after the move." Keep this output on screen. |
| 4 | WishBridge: **Assessment** | Open the SSIS folder, read the warehouse database, Run. |
| 5 | WishBridge: **Report** | Effort estimate, ready/review/fix files, and **what the packages use**: found vs missing (`usp_SendLoadReport`). |
| 6 | WishBridge: **Code migration** | Fix code → Settings → Deploy → deployment report. Show the tables and code in Databricks. |
| 7 | WishBridge: **Data migration** | Map the data → Check → Copy → data report with rows per table. |
| 8 | Databricks: run `04_verify_databricks.sql` | Put it next to step 3: **every number matches**. Then download the sign-off package. |

### What the client's system does (step 1-2)

- **`Master.dtsx`** runs the nightly load: first `LoadDimCustomer`, then `LoadFactSales`.
- **`LoadDimCustomer.dtsx`** empties `stg.Customer`, copies **active** customers from the order system into it
  (full name, e-mail domain), then `MERGE`s them into `dw.DimCustomer` (new customers added, changed ones updated).
- **`LoadFactSales.dtsx`**
  1. `usp_ArchiveOldSales` moves days older than 2 years to `FactDailySales_Archive`;
  2. deletes the last 7 days and reloads them from the orders: per day and customer, order count, quantity and
     amount (`qty × price × (1 − discount)`), **cancelled orders and inactive customers left out**;
  3. `usp_RefreshSalesSummary` rebuilds the monthly summary per country;
  4. calls `usp_SendLoadReport`, which **nobody created**: a finding the assessment must catch.
- **In the database**: 7 procedures (the same loads, used for the first full load), `fn_FiscalYear`
  (fiscal year starts in April), and 2 report views (`vCustomerSales`, `vMonthlySales`).
- **Why the numbers differ from the source**: 500 customers → 480 (20 inactive); 20,000 orders → 18,700
  (500 cancelled, plus the orders of inactive customers) - the business rules at work.

### The numbers that must match (step 3 and 8)

| Check | Rows | Total 1 | Total 2 |
|---|---|---|---|
| DimCustomer: rows, countries, customer IDs | 480 | 5 | 120,000 |
| DimProduct: rows, categories, list price | 40 | 4 | 16,919.60 |
| DimDate: rows, years, date keys | 1,462 | 5 | 29,610,626,995 |
| FactDailySales: rows, orders, amount | 18,478 | 18,700 | 63,499,363.80 |
| FactDailySales_Archive | 0 | 0 | 0 |
| SalesSummary: rows, orders, amount | 125 | 18,700 | 63,499,363.80 |
| LoadLog: rows, rows loaded | 1 | 18,478 | 0 |

(Taken on 14 Oct 2026. Re-running `02_DemoSalesDW.sql` on another day shifts the dates, so always run
`03_verify_sqlserver.sql` fresh before the demo.)

## What is in this folder

| Folder | What |
|---|---|
| `setup\01_DemoSalesOLTP.sql` | Creates the source database: 500 customers, 40 products, 20,000 orders, ~50,000 order lines |
| `setup\02_DemoSalesDW.sql` | Creates the warehouse: 8 tables, 7 procedures, 2 views, 1 function, and runs the first load |
| `setup\export_for_databricks.py` | Exports the warehouse tables to Parquet (for the data migration) |
| `setup\03_verify_sqlserver.sql` | Counts and totals per table in SQL Server (before) |
| `setup\04_verify_databricks.sql` | The same counts and totals in Databricks (after) |
| `SSIS\SalesDW_ETL\` | The SSIS project: `Master.dtsx`, `LoadDimCustomer.dtsx`, `LoadFactSales.dtsx` |
| `export\` | The Parquet export, one folder per table |

Everything runs on `.\SQLEXPRESS` with your Windows login. To rebuild from scratch (safe to re-run):

```bash
sqlcmd -S .\SQLEXPRESS -E -C -i C:\WishBridgeDemo\setup\01_DemoSalesOLTP.sql
```
```bash
sqlcmd -S .\SQLEXPRESS -E -C -i C:\WishBridgeDemo\setup\02_DemoSalesDW.sql
```
```bash
C:\wishbridge\wishbridge\.venv\Scripts\python.exe C:\WishBridgeDemo\setup\export_for_databricks.py
```

### Built-in findings, so the demo has something to show

- `LoadFactSales.dtsx` calls **`dw.usp_SendLoadReport`**, which does not exist in the database:
  the assessment reports it as **missing** (a typical "the package calls something nobody has" finding).
- `Sales.Customer`, `Sales.Orders`, `Sales.OrderLines` are also listed as missing. That is correct:
  they live in the source system (`DemoSalesOLTP`), not in the warehouse.
- `usp_ArchiveOldSales`, `usp_RefreshSalesSummary` and the `dw` / `stg` tables are **found**.
- The procedures use T-SQL that needs review in Databricks (`MERGE ... OUTPUT`, `TRY/CATCH`, `@@ROWCOUNT`,
  temp tables, `IDENTITY`), so the conversion shows ready / review / needs-fix files.

## Before you start

1. Start WishBridge (restart it if it was already open, so it picks up the latest code):
   ```bash
   C:\wishbridge\wishbridge\.venv\Scripts\python.exe -m wishbridge ui
   ```
2. **Sign in to Databricks again.** Your DEFAULT login has expired. The assessment doesn't need it,
   but code migration and data migration do:
   ```bash
   databricks auth login --profile DEFAULT
   ```
3. In this workspace the catalog **`main` does not exist**. Use **`workspace`** (or `test`).

## 1. Assessment (offline, no Databricks)

1. First page: **🔍 Assessment** → **Open a folder** → `C:\WishBridgeDemo\SSIS` → open.
   Keep the project in the suggested `C:\WishBridge\Assessments\...` folder
   (deep folders can exceed Windows' 260-character path limit during analysis).
2. **Recommended: the warehouse database these packages load**, which reads the warehouse's code and table definitions (no data):
   - Server: `.\SQLEXPRESS` · Database: `DemoSalesDW`
   - Login: **Windows login** · tick **Trust the server certificate**
   - Result: 8 tables, 10 code objects (procedures, views, function).
3. **Run**: describe, analyze, convert, report, zip (about 2 minutes).
   Expect: 13 files analysed, ~8 h estimate, convert ≈ 6 ready / 8 review / 1 needs fixes.
4. **Report**: check *What the packages use*: `usp_SendLoadReport` missing, the archive/refresh procedures found.
5. The zip is in the project's `output\review_package\`. That is what you would take back from the client.

## 2. Code migration (needs the Databricks login)

1. First page: **🧩 Code migration** → **Open the assessment zip** → the zip from step 1.5.
2. **Fix code**: open the files marked *needs fixes* / *review*, correct them, save; then **Next: settings →**.
3. **Settings**: workspace login `DEFAULT` (signed in), catalog **`workspace`**, schema e.g. `salesdw_demo`,
   a SQL warehouse. **Save and next** checks the catalog, schema and warehouse before moving on.
4. **Deploy**: creates the tables, views, procedures/functions in `workspace.salesdw_demo`.
5. **Deployment report**: what was deployed and what failed. Then **Start the data migration →**.

## 3. Data migration (needs the Databricks login)

*How to copy the data* offers three ways - show all three, use the second for the demo:

| Option | When |
|---|---|
| Client's database, read directly by Databricks (Lakehouse Federation) | At a client: Databricks reads their warehouse over the network with a read-only SQL user |
| **A database on this computer (WishBridge exports and uploads it)** | **The demo** (and any database Databricks cannot reach) |
| Exported files already in a Unity Catalog volume | The client sends exports themselves |

Say: *"Today it reads my laptop; at a client we pick the first option and Databricks reads their warehouse directly."*

1. **🚚 Data migration** → **Open the code migration project** (or continue from the deployment report).
2. **Settings**: the workspace and target are already settled from the code migration.
   *How to copy the data*: **A database on this computer** → server `.\SQLEXPRESS`, database `DemoSalesDW`,
   **Windows login**, *Trust the server certificate* ticked. The upload folder is filled in
   (`/Volumes/<catalog>/<schema>/wishbridge_landing`); WishBridge creates the volume.
3. **Map the data**: **🔄 Propose the mapping**, check the columns, **🔎 Check before copying**
   (it reads the local database and creates the volume), then **▶ Copy the data**.
   For each table WishBridge reads it (read-only), saves it as Parquet, uploads it and copies it into the
   Databricks table with every column cast to the table's type.
4. **Data report**: rows per table, and the comparison - row counts and the sum of every number column,
   **counted in SQL Server on this computer** against Databricks (DimCustomer 480, FactDailySales 18,478,
   amount 63,499,363.80, ...). Then the sign-off package.

The manual way still works: `setup\export_for_databricks.py`, upload `C:\WishBridgeDemo\export` with
`databricks fs cp -r ... dbfs:/Volumes/...`, and choose *Exported files already in a Unity Catalog volume*.

## Expected numbers (after the first load)

| Table | Rows |
|---|---|
| dw.DimCustomer | 480 |
| dw.DimDate | 1,462 |
| dw.DimProduct | 40 |
| dw.FactDailySales | 18,478 |
| dw.FactDailySales_Archive | 0 (filled when `usp_ArchiveOldSales` runs) |
| dw.SalesSummary | 125 |
| dw.LoadLog | 1 |
| stg.Customer | 480 (staging, not exported) |

## If something goes wrong

- **Analyze fails with "path is too long for Windows"**: move the project to a shorter folder (e.g. `C:\WishBridge\...`).
- **Settings: "login expired"**: run `databricks auth login --profile DEFAULT` and click *Load warehouses and catalogs*.
- **Catalog `main` not found**: use `workspace`.
- **Cannot connect to `.\SQLEXPRESS`**: check the *SQL Server (SQLEXPRESS)* service is running (services.msc).
