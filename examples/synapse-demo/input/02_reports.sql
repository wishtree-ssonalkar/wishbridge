-- Customer spend summary (CTAS, the usual Synapse pattern)
CREATE TABLE dbo.CustomerSpend
WITH (DISTRIBUTION = HASH(CustomerID))
AS
SELECT c.CustomerID, c.FullName, SUM(o.Amount) AS TotalSpend, COUNT_BIG(*) AS OrderCount
FROM dbo.Customers c
JOIN dbo.Orders o ON o.CustomerID = c.CustomerID
WHERE o.Status <> 'Cancelled'
GROUP BY c.CustomerID, c.FullName;

-- Top three customers
SELECT TOP 3 CustomerID, FullName, TotalSpend
FROM dbo.CustomerSpend
ORDER BY TotalSpend DESC;
