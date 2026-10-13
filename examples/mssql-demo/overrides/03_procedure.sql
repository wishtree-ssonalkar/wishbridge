-- Manual fix (WishBridge override): Databricks has no @@ROWCOUNT, so the number of rows
-- to close is counted before the UPDATE and returned instead.
CREATE OR REPLACE PROCEDURE workspace.wishbridge_demo.usp_CloseStaleOrders(IN DaysOld INT DEFAULT 30)
    LANGUAGE SQL
    SQL SECURITY INVOKER
AS
BEGIN
    DECLARE rows_closed INT DEFAULT 0;

    SET rows_closed = (
        SELECT COUNT(*)
        FROM workspace.wishbridge_demo.Orders
        WHERE Status = 'Open' AND DATEDIFF(CURRENT_TIMESTAMP(), OrderDate) > DaysOld
    );

    UPDATE workspace.wishbridge_demo.Orders
    SET Status = 'Closed'
    WHERE Status = 'Open' AND DATEDIFF(CURRENT_TIMESTAMP(), OrderDate) > DaysOld;

    SELECT rows_closed AS RowsClosed;
END;
