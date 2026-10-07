CREATE PROCEDURE dbo.usp_MonthlyRevenue @Year INT
AS
BEGIN
    SELECT MONTH(OrderDate) AS Mth, SUM(Amount) AS Revenue
    FROM dbo.Orders
    WHERE YEAR(OrderDate) = @Year AND Status <> 'Cancelled'
    GROUP BY MONTH(OrderDate)
    ORDER BY Mth;
END;
