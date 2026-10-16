-- WishBridge demo: the DATA WAREHOUSE the SSIS packages load (staging, dimensions, facts, procedures, views).
-- Run AFTER 01_DemoSalesOLTP.sql:  sqlcmd -S .\SQLEXPRESS -E -C -i 02_DemoSalesDW.sql
-- Re-running it rebuilds the database from scratch (only DemoSalesDW is touched), then loads it from the source.
SET NOCOUNT ON;
GO
IF DB_ID('DemoSalesDW') IS NOT NULL
BEGIN
    ALTER DATABASE DemoSalesDW SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE DemoSalesDW;
END
GO
CREATE DATABASE DemoSalesDW;
GO
USE DemoSalesDW;
GO
CREATE SCHEMA stg;
GO
CREATE SCHEMA dw;
GO

-- ------------------------------------------------------------------ tables
CREATE TABLE stg.Customer (                     -- loaded by LoadDimCustomer.dtsx
    CustomerID         INT           NOT NULL,
    FullName           NVARCHAR(101) NULL,
    Email              VARCHAR(120)  NULL,
    EmailDomain        VARCHAR(80)   NULL,
    City               NVARCHAR(60)  NULL,
    Country            NVARCHAR(60)  NULL,
    SourceModifiedDate DATETIME2(0)  NULL,
    LoadDate           DATETIME2(0)  NULL
);

CREATE TABLE dw.DimCustomer (
    CustomerKey INT IDENTITY(1, 1) NOT NULL CONSTRAINT PK_DimCustomer PRIMARY KEY,
    CustomerID  INT           NOT NULL CONSTRAINT UQ_DimCustomer_CustomerID UNIQUE,
    FullName    NVARCHAR(101) NOT NULL,
    Email       VARCHAR(120)  NULL,
    EmailDomain VARCHAR(80)   NULL,
    City        NVARCHAR(60)  NULL,
    Country     NVARCHAR(60)  NULL,
    CreatedDate DATETIME2(0)  NOT NULL,
    UpdatedDate DATETIME2(0)  NULL
);

CREATE TABLE dw.DimProduct (
    ProductKey  INT IDENTITY(1, 1) NOT NULL CONSTRAINT PK_DimProduct PRIMARY KEY,
    ProductID   INT            NOT NULL CONSTRAINT UQ_DimProduct_ProductID UNIQUE,
    ProductName NVARCHAR(80)   NOT NULL,
    Category    NVARCHAR(40)   NOT NULL,
    ListPrice   DECIMAL(10, 2) NOT NULL
);

CREATE TABLE dw.DimDate (
    DateKey    INT         NOT NULL CONSTRAINT PK_DimDate PRIMARY KEY,   -- yyyymmdd
    [Date]     DATE        NOT NULL,
    [Year]     SMALLINT    NOT NULL,
    [Quarter]  TINYINT     NOT NULL,
    [Month]    TINYINT     NOT NULL,
    MonthName  VARCHAR(10) NOT NULL,
    FiscalYear SMALLINT    NOT NULL
);

CREATE TABLE dw.FactDailySales (                -- loaded by LoadFactSales.dtsx
    OrderDate     DATE           NOT NULL,
    CustomerKey   INT            NOT NULL,
    OrderCount    BIGINT         NOT NULL,
    TotalQuantity BIGINT         NOT NULL,
    TotalAmount   DECIMAL(18, 4) NOT NULL,
    CONSTRAINT PK_FactDailySales PRIMARY KEY (OrderDate, CustomerKey)
);

CREATE TABLE dw.FactDailySales_Archive (
    OrderDate     DATE           NOT NULL,
    CustomerKey   INT            NOT NULL,
    OrderCount    BIGINT         NOT NULL,
    TotalQuantity BIGINT         NOT NULL,
    TotalAmount   DECIMAL(18, 4) NOT NULL,
    ArchivedAt    DATETIME2(0)   NOT NULL
);

CREATE TABLE dw.SalesSummary (
    MonthStart  DATE           NOT NULL,
    Country     NVARCHAR(60)   NOT NULL,
    Customers   INT            NOT NULL,
    Orders      BIGINT         NOT NULL,
    Amount      DECIMAL(18, 4) NOT NULL,
    RefreshedAt DATETIME2(0)   NOT NULL,
    CONSTRAINT PK_SalesSummary PRIMARY KEY (MonthStart, Country)
);

CREATE TABLE dw.LoadLog (
    LoadLogID  INT IDENTITY(1, 1) NOT NULL PRIMARY KEY,
    StepName   VARCHAR(100)  NOT NULL,
    RowsLoaded INT           NULL,
    StartedAt  DATETIME2(0)  NOT NULL,
    FinishedAt DATETIME2(0)  NULL,
    Message    NVARCHAR(400) NULL
);
GO

-- ------------------------------------------------------------------ function
CREATE FUNCTION dw.fn_FiscalYear (@d DATE)
RETURNS SMALLINT
AS
BEGIN
    -- Fiscal year starts on 1 April
    RETURN CASE WHEN MONTH(@d) >= 4 THEN YEAR(@d) + 1 ELSE YEAR(@d) END;
END
GO

-- ------------------------------------------------------------------ procedures (the warehouse's load logic)
CREATE PROCEDURE dw.usp_LoadDimDate @From DATE, @To DATE
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @d DATE = @From;
    WHILE @d <= @To
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM dw.DimDate WHERE [Date] = @d)
            INSERT dw.DimDate (DateKey, [Date], [Year], [Quarter], [Month], MonthName, FiscalYear)
            VALUES (CONVERT(INT, CONVERT(CHAR(8), @d, 112)), @d, YEAR(@d), DATEPART(QUARTER, @d), MONTH(@d),
                    DATENAME(MONTH, @d), dw.fn_FiscalYear(@d));
        SET @d = DATEADD(DAY, 1, @d);
    END
END
GO

CREATE PROCEDURE dw.usp_LoadDimProduct
AS
BEGIN
    SET NOCOUNT ON;
    MERGE dw.DimProduct AS tgt
    USING (SELECT ProductID, ProductName, Category, ListPrice FROM DemoSalesOLTP.Sales.Product WITH (NOLOCK)) AS src
        ON tgt.ProductID = src.ProductID
    WHEN MATCHED AND (tgt.ProductName <> src.ProductName OR tgt.ListPrice <> src.ListPrice) THEN
        UPDATE SET tgt.ProductName = src.ProductName, tgt.Category = src.Category, tgt.ListPrice = src.ListPrice
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (ProductID, ProductName, Category, ListPrice)
        VALUES (src.ProductID, src.ProductName, src.Category, src.ListPrice);
END
GO

-- What LoadDimCustomer.dtsx does, as a procedure (used for the initial load and when SSIS is not available)
CREATE PROCEDURE dw.usp_LoadDimCustomer
AS
BEGIN
    SET NOCOUNT ON;
    TRUNCATE TABLE stg.Customer;
    INSERT stg.Customer (CustomerID, FullName, Email, EmailDomain, City, Country, SourceModifiedDate, LoadDate)
    SELECT c.CustomerID, LTRIM(RTRIM(c.FirstName)) + N' ' + LTRIM(RTRIM(c.LastName)), LOWER(c.Email),
           LOWER(SUBSTRING(c.Email, CHARINDEX('@', c.Email) + 1, 80)), c.City, c.Country, c.ModifiedDate, GETDATE()
    FROM DemoSalesOLTP.Sales.Customer AS c WITH (NOLOCK)
    WHERE c.IsActive = 1;

    MERGE dw.DimCustomer AS tgt
    USING stg.Customer AS src
        ON tgt.CustomerID = src.CustomerID
    WHEN MATCHED AND (tgt.FullName <> src.FullName OR tgt.Email <> src.Email OR tgt.City <> src.City
                      OR tgt.Country <> src.Country) THEN
        UPDATE SET tgt.FullName = src.FullName, tgt.Email = src.Email, tgt.EmailDomain = src.EmailDomain,
                   tgt.City = src.City, tgt.Country = src.Country, tgt.UpdatedDate = GETDATE()
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (CustomerID, FullName, Email, EmailDomain, City, Country, CreatedDate, UpdatedDate)
        VALUES (src.CustomerID, src.FullName, src.Email, src.EmailDomain, src.City, src.Country, GETDATE(), NULL);
END
GO

-- What LoadFactSales.dtsx does, as a procedure; @Days NULL = full history
CREATE PROCEDURE dw.usp_LoadFactDailySales @Days INT = 7
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @From DATE = CASE WHEN @Days IS NULL THEN '19000101' ELSE DATEADD(DAY, -@Days, CAST(GETDATE() AS DATE)) END;
    DECLARE @Started DATETIME2(0) = SYSDATETIME(), @Rows INT;
    BEGIN TRY
        BEGIN TRANSACTION;
        DELETE FROM dw.FactDailySales WHERE OrderDate >= @From;

        INSERT dw.FactDailySales (OrderDate, CustomerKey, OrderCount, TotalQuantity, TotalAmount)
        SELECT CAST(o.OrderDate AS DATE), dc.CustomerKey, COUNT(DISTINCT o.OrderID), SUM(ol.Quantity),
               SUM(ol.Quantity * ol.UnitPrice * (1 - ol.Discount))
        FROM DemoSalesOLTP.Sales.Orders AS o
        JOIN DemoSalesOLTP.Sales.OrderLines AS ol ON ol.OrderID = o.OrderID
        JOIN dw.DimCustomer AS dc ON dc.CustomerID = o.CustomerID
        WHERE o.OrderDate >= @From AND o.Status <> 'Cancelled'
        GROUP BY CAST(o.OrderDate AS DATE), dc.CustomerKey;
        SET @Rows = @@ROWCOUNT;
        COMMIT TRANSACTION;

        INSERT dw.LoadLog (StepName, RowsLoaded, StartedAt, FinishedAt)
        VALUES ('LoadFactDailySales', @Rows, @Started, SYSDATETIME());
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        INSERT dw.LoadLog (StepName, StartedAt, FinishedAt, Message)
        VALUES ('LoadFactDailySales', @Started, SYSDATETIME(), ERROR_MESSAGE());
        RAISERROR ('LoadFactDailySales failed: %s', 16, 1, 'see dw.LoadLog');
    END CATCH
END
GO

-- Called by LoadFactSales.dtsx after the daily load
CREATE PROCEDURE dw.usp_RefreshSalesSummary
AS
BEGIN
    SET NOCOUNT ON;
    TRUNCATE TABLE dw.SalesSummary;
    INSERT dw.SalesSummary (MonthStart, Country, Customers, Orders, Amount, RefreshedAt)
    SELECT DATEFROMPARTS(YEAR(f.OrderDate), MONTH(f.OrderDate), 1), ISNULL(c.Country, N'Unknown'),
           COUNT(DISTINCT f.CustomerKey), SUM(f.OrderCount), SUM(f.TotalAmount), GETDATE()
    FROM dw.FactDailySales AS f
    JOIN dw.DimCustomer AS c ON c.CustomerKey = f.CustomerKey
    GROUP BY DATEFROMPARTS(YEAR(f.OrderDate), MONTH(f.OrderDate), 1), ISNULL(c.Country, N'Unknown');
END
GO

-- Called by LoadFactSales.dtsx before the daily load: moves old days to the archive
CREATE PROCEDURE dw.usp_ArchiveOldSales @KeepDays INT = 730
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM dw.FactDailySales
    OUTPUT DELETED.OrderDate, DELETED.CustomerKey, DELETED.OrderCount, DELETED.TotalQuantity, DELETED.TotalAmount,
           SYSDATETIME()
    INTO dw.FactDailySales_Archive (OrderDate, CustomerKey, OrderCount, TotalQuantity, TotalAmount, ArchivedAt)
    WHERE OrderDate < DATEADD(DAY, -@KeepDays, CAST(GETDATE() AS DATE));
END
GO

-- The full initial load (what the nightly SSIS run keeps up to date)
CREATE PROCEDURE dw.usp_LoadAll
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @From DATE = DATEADD(YEAR, -3, CAST(GETDATE() AS DATE)), @To DATE = DATEADD(YEAR, 1, CAST(GETDATE() AS DATE));
    EXEC dw.usp_LoadDimDate @From, @To;
    EXEC dw.usp_LoadDimProduct;
    EXEC dw.usp_LoadDimCustomer;
    EXEC dw.usp_LoadFactDailySales @Days = NULL;
    EXEC dw.usp_RefreshSalesSummary;
END
GO

-- ------------------------------------------------------------------ views (for reports)
CREATE VIEW dw.vCustomerSales
AS
SELECT c.CustomerID, c.FullName, c.Country, SUM(f.OrderCount) AS Orders, SUM(f.TotalAmount) AS Amount,
       MAX(f.OrderDate) AS LastOrderDate
FROM dw.FactDailySales AS f
JOIN dw.DimCustomer AS c ON c.CustomerKey = f.CustomerKey
GROUP BY c.CustomerID, c.FullName, c.Country;
GO

CREATE VIEW dw.vMonthlySales
AS
SELECT d.FiscalYear, d.[Year], d.[Month], d.MonthName, SUM(f.OrderCount) AS Orders, SUM(f.TotalAmount) AS Amount
FROM dw.FactDailySales AS f
JOIN dw.DimDate AS d ON d.[Date] = f.OrderDate
GROUP BY d.FiscalYear, d.[Year], d.[Month], d.MonthName;
GO

-- ------------------------------------------------------------------ initial load
EXEC dw.usp_LoadAll;
GO
DECLARE @c INT = (SELECT COUNT(*) FROM dw.DimCustomer), @p INT = (SELECT COUNT(*) FROM dw.DimProduct),
        @d INT = (SELECT COUNT(*) FROM dw.DimDate), @f INT = (SELECT COUNT(*) FROM dw.FactDailySales),
        @s INT = (SELECT COUNT(*) FROM dw.SalesSummary);
PRINT CONCAT('DemoSalesDW ready: ', @c, ' customers, ', @p, ' products, ', @d, ' dates, ', @f, ' daily sales rows, ',
             @s, ' summary rows.');
GO
