# WishBridge local demo: SSIS + SQL Server

A complete client setup on this laptop, for testing every WishBridge phase end to end:
an order-entry system, a data warehouse with stored procedures, and the SSIS packages that load it.

```
DemoSalesOLTP (source system)  --SSIS packages-->  DemoSalesDW (warehouse: tables, procedures, views, function)
   Sales.Customer / Product /                         stg.Customer, dw.DimCustomer, DimProduct, DimDate,
   Orders / OrderLines                                FactDailySales (+ _Archive), SalesSummary, LoadLog
```

## What is in this folder

| Folder | What |
|---|---|
| `setup\01_DemoSalesOLTP.sql` | Creates the source database: 500 customers, 40 products, 20,000 orders, ~50,000 order lines |
| `setup\02_DemoSalesDW.sql` | Creates the warehouse: 8 tables, 7 procedures, 2 views, 1 function, and runs the first load |
| `setup\export_for_databricks.py` | Exports the warehouse tables to Parquet (for the data migration) |
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

Databricks runs in the cloud and cannot reach SQL Server Express on this laptop, so the data travels as files.

1. Create a volume and upload the export (once). In a Databricks SQL editor:
   ```sql
   CREATE SCHEMA IF NOT EXISTS workspace.wishbridge_demo;
   CREATE VOLUME IF NOT EXISTS workspace.wishbridge_demo.landing;
   ```
   then upload:
   ```bash
   databricks fs cp -r C:\WishBridgeDemo\export dbfs:/Volumes/workspace/wishbridge_demo/landing --profile DEFAULT
   ```
2. **🚚 Data migration** → **Open the code migration project** (or continue from the deployment report).
3. **Settings**: the workspace and target are already settled from the code migration.
   *How to copy the data*: **Exported files in a Unity Catalog volume** →
   files root `/Volumes/workspace/wishbridge_demo/landing`, format `PARQUET`.
4. **Map the data**: **🔄 Propose the mapping** (each export folder → its table), check the columns,
   **🔎 Check before copying**, then **▶ Copy the data**.
5. **Data report**: rows per table (DimCustomer 480, FactDailySales 18,478, ...), then the sign-off package.

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
