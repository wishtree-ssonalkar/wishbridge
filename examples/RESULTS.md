# WishBridge example results

Run 2026-10-08 11:25 with `python examples/run_all.py` on a Databricks SQL warehouse.

| example | source | converter | files | converted | open | deployed | loaded | notebooks | reconciled | result | seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mssql | MS SQL Server | morph | 4 | 2 ready, 2 hand-fixed, 0 other | 0 | 6/6 | 2/2 | - | 2/2 | PASS | 117 |
| synapse | Synapse | morph | 3 | 3 ready, 0 hand-fixed, 0 other | 0 | 5/5 | 2/2 | - | 2/2 | PASS | 56 |
| oracle | Oracle | morph | 3 | 0 ready, 2 hand-fixed, 1 other | 2 | 5/5 | 2/2 | - | 2/2 | PASS | 89 |
| snowflake | Snowflake | morph | 3 | 2 ready, 1 hand-fixed, 0 other | 0 | 6/6 | 2/2 | - | 2/2 | PASS | 55 |
| teradata | Teradata | bladebridge | 3 | 0 ready, 2 hand-fixed, 1 other | 1 | 5/5 | 2/2 | - | 2/2 | PASS | 94 |
| redshift | Redshift | morph | 3 | 2 ready, 1 hand-fixed, 0 other | 0 | 7/7 | 2/2 | - | 2/2 | PASS | 77 |
| bigquery | BigQuery | morph | 3 | 1 ready, 2 hand-fixed, 0 other | 0 | 6/6 | 2/2 | - | 2/2 | PASS | 54 |
| netezza | Netezza | bladebridge | 3 | 0 ready, 3 hand-fixed, 0 other | 0 | 7/7 | 2/2 | - | 2/2 | PASS | 74 |
| informatica | Informatica - PC | bladebridge | - | 1 ready, 1 hand-fixed, 1 other | 0 | 4/4 | 1/1 | 1/1 | 2/2 | PASS | 142 |

*converted*: files ready straight from LakeBridge + WishBridge rules / files fixed by hand in `overrides/` / files still open. *open*: open errors and warnings on the remaining files. *deployed*: statements created or EXPLAIN-checked on Databricks. *reconciled*: tables whose counts, sums and row checksums match the source.
