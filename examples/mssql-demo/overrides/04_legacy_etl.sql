-- Manual fix (WishBridge override): the transpiler turned UPDATE ... FROM ... JOIN #temp into
-- `MERGE INTO c` (an alias). Rewritten as a MERGE into the real table, with the temp table
-- replaced by a subquery and PRINT dropped.
CREATE OR REPLACE PROCEDURE workspace.wishbridge_demo.usp_RefreshCustomerValue()
    LANGUAGE SQL
    SQL SECURITY INVOKER
AS
BEGIN
    MERGE INTO workspace.wishbridge_demo.Customers AS c
    USING (
        SELECT CustomerID, SUM(Amount) AS LifetimeValue
        FROM workspace.wishbridge_demo.Orders
        WHERE Status != 'Cancelled'
        GROUP BY CustomerID
    ) AS v
    ON v.CustomerID = c.CustomerID AND v.LifetimeValue > 1000
    WHEN MATCHED THEN UPDATE SET c.Email = LOWER(c.Email);
END;
