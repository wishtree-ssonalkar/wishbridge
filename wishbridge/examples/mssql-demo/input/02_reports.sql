-- Top 10 customers by spend in the last 90 days
SELECT TOP 10
    c.CustomerID,
    c.FullName,
    ISNULL(SUM(o.Amount), 0) AS TotalSpend,
    COUNT(o.OrderID) AS OrderCount,
    CONVERT(VARCHAR(10), MAX(o.OrderDate), 120) AS LastOrderDate
FROM dbo.Customers c
LEFT JOIN dbo.Orders o
    ON o.CustomerID = c.CustomerID
   AND o.OrderDate >= DATEADD(DAY, -90, GETDATE())
GROUP BY c.CustomerID, c.FullName
ORDER BY TotalSpend DESC;

-- Monthly revenue
SELECT
    YEAR(OrderDate) AS Yr,
    MONTH(OrderDate) AS Mth,
    SUM(Amount) AS Revenue,
    IIF(SUM(Amount) > 10000, 'High', 'Normal') AS Band
FROM dbo.Orders
WHERE Status <> 'Cancelled'
GROUP BY YEAR(OrderDate), MONTH(OrderDate);
