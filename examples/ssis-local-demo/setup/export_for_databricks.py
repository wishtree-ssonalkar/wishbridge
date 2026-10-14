"""Export the demo warehouse tables to Parquet, for WishBridge's data migration with "Exported files".

Databricks runs in the cloud and cannot connect to SQL Server Express on this laptop, so for the demo the data
travels as files: run this, upload the folder to a Unity Catalog volume, and point WishBridge at it.

    C:\\wishbridge\\wishbridge\\.venv\\Scripts\\python.exe export_for_databricks.py

Writes C:\\WishBridgeDemo\\export\\<Table>\\<Table>.parquet - one folder per table, the layout WishBridge's
"Exported files" copy expects (<files root>/<table>/).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyodbc

SERVER = r".\SQLEXPRESS"
DATABASE = "DemoSalesDW"
TABLES = ["dw.DimCustomer", "dw.DimProduct", "dw.DimDate", "dw.FactDailySales", "dw.FactDailySales_Archive",
          "dw.SalesSummary", "dw.LoadLog"]
OUT = Path(r"C:\WishBridgeDemo\export")


def driver() -> str:
    for d in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server"):
        if d in pyodbc.drivers():
            return d
    raise SystemExit("No SQL Server ODBC driver found.")


def main() -> None:
    conn = pyodbc.connect(f"DRIVER={{{driver()}}};SERVER={SERVER};DATABASE={DATABASE};Trusted_Connection=yes;"
                          "Encrypt=yes;TrustServerCertificate=yes", readonly=True)
    try:
        for table in TABLES:
            name = table.split(".")[-1]
            df = pd.read_sql(f"SELECT * FROM {table}", conn)
            folder = OUT / name
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob("*.parquet"):
                old.unlink()
            df.to_parquet(folder / f"{name}.parquet", index=False)
            print(f"{table:28} {len(df):>7,} rows -> {folder / (name + '.parquet')}")
    finally:
        conn.close()
    print(f"\nDone. Upload the folder {OUT} to a Unity Catalog volume, e.g. /Volumes/workspace/demo/landing")


if __name__ == "__main__":
    main()
