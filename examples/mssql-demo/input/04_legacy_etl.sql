-- Nightly customer value refresh (legacy ETL)
CREATE PROCEDURE [dbo].[usp_RefreshCustomerValue]
AS
BEGIN
    SET NOCOUNT ON;

    SELECT o.[CustomerID], SUM(o.[Amount]) AS LifetimeValue
    INTO #CustomerValue
    FROM [dbo].[Orders] o WITH (NOLOCK)
    WHERE o.[Status] <> 'Cancelled'
    GROUP BY o.[CustomerID];

    UPDATE c
    SET c.[Email] = LOWER(c.[Email])
    FROM [dbo].[Customers] c
    INNER JOIN #CustomerValue v ON v.CustomerID = c.CustomerID
    WHERE v.LifetimeValue > 1000;

    PRINT 'Rows updated: ' + CAST(@@ROWCOUNT AS VARCHAR(10));

    DROP TABLE #CustomerValue;
END;
GO
