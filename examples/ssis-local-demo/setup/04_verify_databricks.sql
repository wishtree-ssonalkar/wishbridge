-- WishBridge demo: the numbers AFTER the migration (Databricks SQL editor). Compare with 03_verify_sqlserver.sql:
-- every line must match. Change workspace.salesdw_demo if you used another catalog or schema.
USE workspace.salesdw_demo;

SELECT check_name, rows_, total_1, total_2 FROM (
    SELECT 1 AS n, 'DimCustomer: rows, countries, customer IDs' AS check_name, COUNT(*) AS rows_,
           CAST(COUNT(DISTINCT Country) AS DECIMAL(18, 2)) AS total_1, CAST(SUM(CAST(CustomerID AS BIGINT)) AS DECIMAL(18, 2)) AS total_2
    FROM DimCustomer
    UNION ALL
    SELECT 2, 'DimProduct: rows, categories, list price', COUNT(*), COUNT(DISTINCT Category), SUM(ListPrice)
    FROM DimProduct
    UNION ALL
    SELECT 3, 'DimDate: rows, years, date keys', COUNT(*), COUNT(DISTINCT `Year`), SUM(CAST(DateKey AS BIGINT))
    FROM DimDate
    UNION ALL
    SELECT 4, 'FactDailySales: rows, orders, amount', COUNT(*), SUM(OrderCount), CAST(SUM(TotalAmount) AS DECIMAL(18, 2))
    FROM FactDailySales
    UNION ALL
    SELECT 5, 'FactDailySales_Archive: rows, orders, amount', COUNT(*), COALESCE(SUM(OrderCount), 0),
           CAST(COALESCE(SUM(TotalAmount), 0) AS DECIMAL(18, 2))
    FROM FactDailySales_Archive
    UNION ALL
    SELECT 6, 'SalesSummary: rows, orders, amount', COUNT(*), SUM(Orders), CAST(SUM(Amount) AS DECIMAL(18, 2))
    FROM SalesSummary
    UNION ALL
    SELECT 7, 'LoadLog: rows, rows loaded, -', COUNT(*), COALESCE(SUM(CAST(RowsLoaded AS BIGINT)), 0), 0
    FROM LoadLog
) AS c
ORDER BY n;
