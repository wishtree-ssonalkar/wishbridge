-- WishBridge demo: the numbers BEFORE the migration (SQL Server). Run 04_verify_databricks.sql in Databricks
-- after the data migration: every line must match.
--   sqlcmd -S .\SQLEXPRESS -E -C -d DemoSalesDW -W -s "|" -i 03_verify_sqlserver.sql
SET NOCOUNT ON;
SELECT check_name, rows_, total_1, total_2 FROM (
    SELECT 1 AS n, 'DimCustomer: rows, countries, customer IDs' AS check_name, COUNT(*) AS rows_,
           CAST(COUNT(DISTINCT Country) AS DECIMAL(18, 2)) AS total_1, CAST(SUM(CAST(CustomerID AS BIGINT)) AS DECIMAL(18, 2)) AS total_2
    FROM dw.DimCustomer
    UNION ALL
    SELECT 2, 'DimProduct: rows, categories, list price', COUNT(*), COUNT(DISTINCT Category), SUM(ListPrice)
    FROM dw.DimProduct
    UNION ALL
    SELECT 3, 'DimDate: rows, years, date keys', COUNT(*), COUNT(DISTINCT [Year]), SUM(CAST(DateKey AS BIGINT))
    FROM dw.DimDate
    UNION ALL
    SELECT 4, 'FactDailySales: rows, orders, amount', COUNT(*), SUM(OrderCount), CAST(SUM(TotalAmount) AS DECIMAL(18, 2))
    FROM dw.FactDailySales
    UNION ALL
    SELECT 5, 'FactDailySales_Archive: rows, orders, amount', COUNT(*), ISNULL(SUM(OrderCount), 0),
           CAST(ISNULL(SUM(TotalAmount), 0) AS DECIMAL(18, 2))
    FROM dw.FactDailySales_Archive
    UNION ALL
    SELECT 6, 'SalesSummary: rows, orders, amount', COUNT(*), SUM(Orders), CAST(SUM(Amount) AS DECIMAL(18, 2))
    FROM dw.SalesSummary
    UNION ALL
    SELECT 7, 'LoadLog: rows, rows loaded, -', COUNT(*), ISNULL(SUM(CAST(RowsLoaded AS BIGINT)), 0), 0
    FROM dw.LoadLog
) AS c
ORDER BY n;
