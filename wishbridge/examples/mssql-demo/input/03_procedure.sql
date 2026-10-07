CREATE PROCEDURE dbo.usp_CloseStaleOrders
    @DaysOld INT = 30
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.Orders
    SET Status = 'Closed'
    WHERE Status = 'Open'
      AND DATEDIFF(DAY, OrderDate, GETDATE()) > @DaysOld;

    SELECT @@ROWCOUNT AS RowsClosed;
END;
