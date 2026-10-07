CREATE
    PROCEDURE main.wishbridge_demo.usp_CloseStaleOrders(IN _DaysOld INT DEFAULT 30)
    LANGUAGE SQL
    SQL SECURITY INVOKER
    AS
        BEGIN
            -- SET NOCOUNT ON - Databricks SQL does not return row count messages, equivalent to NOCOUNT ON
            
            UPDATE main.wishbridge_demo.Orders
            SET Status = 'Closed'
            WHERE Status = 'Open' AND CAST(DATEDIFF(CURRENT_TIMESTAMP(), OrderDate) AS INT) > _DaysOld;
            -- FIXME: T-SQL CURRENT_TIMESTAMP/GETDATE returns datetime with approximately 3.33 ms resolution; Databricks CURRENT_TIMESTAMP() uses microsecond precision
            SELECT @@ROWCOUNT() AS RowsClosed;
        END;