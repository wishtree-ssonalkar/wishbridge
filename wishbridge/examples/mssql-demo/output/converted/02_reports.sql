-- Top 10 customers by spend in the last 90 days
SELECT
    c.CustomerID,
    c.FullName,
    IFNULL(SUM(o.Amount), 0) AS TotalSpend,
    COUNT(o.OrderID) AS OrderCount,
    DATE_FORMAT(MAX(o.OrderDate), 'yyyy-MM-dd HH:mm:ss') AS LastOrderDate
FROM
    dbo.Customers AS c LEFT JOIN dbo.Orders AS o
    ON (o.CustomerID = c.CustomerID AND o.OrderDate >= DATE_ADD(CURRENT_TIMESTAMP(), -90))
    -- FIXME: T-SQL CURRENT_TIMESTAMP/GETDATE returns datetime with approximately 3.33 ms resolution; Databricks CURRENT_TIMESTAMP() uses microsecond precision
    GROUP BY c.CustomerID, c.FullName
ORDER BY TotalSpend DESC
LIMIT 10;

SELECT
    YEAR(OrderDate) AS 
 -- Monthly revenue
 Yr,
    MONTH(OrderDate) AS Mth,
    SUM(Amount) AS Revenue,
    IF(SUM(Amount) > 10000, 'High', 'Normal') AS Band
FROM dbo.Orders WHERE Status != 'Cancelled' GROUP BY YEAR(OrderDate), MONTH(OrderDate);