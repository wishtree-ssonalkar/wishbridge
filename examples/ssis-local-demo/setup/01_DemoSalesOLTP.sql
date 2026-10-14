-- WishBridge demo: the SOURCE system (an order-entry database) that the SSIS packages read from.
-- Run on the local SQL Server Express:  sqlcmd -S .\SQLEXPRESS -E -C -i 01_DemoSalesOLTP.sql
-- Re-running it rebuilds the database from scratch (only DemoSalesOLTP is touched).
SET NOCOUNT ON;
GO
IF DB_ID('DemoSalesOLTP') IS NOT NULL
BEGIN
    ALTER DATABASE DemoSalesOLTP SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE DemoSalesOLTP;
END
GO
CREATE DATABASE DemoSalesOLTP;
GO
USE DemoSalesOLTP;
GO
CREATE SCHEMA Sales;
GO

CREATE TABLE Sales.Customer (
    CustomerID   INT           NOT NULL PRIMARY KEY,
    FirstName    NVARCHAR(50)  NOT NULL,
    LastName     NVARCHAR(50)  NOT NULL,
    Email        VARCHAR(120)  NULL,
    City         NVARCHAR(60)  NULL,
    Country      NVARCHAR(60)  NULL,
    IsActive     BIT           NOT NULL CONSTRAINT DF_Customer_IsActive DEFAULT (1),
    ModifiedDate DATETIME2(0)  NOT NULL CONSTRAINT DF_Customer_Modified DEFAULT (SYSDATETIME())
);

CREATE TABLE Sales.Product (
    ProductID   INT            NOT NULL PRIMARY KEY,
    ProductName NVARCHAR(80)   NOT NULL,
    Category    NVARCHAR(40)   NOT NULL,
    ListPrice   DECIMAL(10, 2) NOT NULL
);

CREATE TABLE Sales.Orders (
    OrderID    INT          NOT NULL PRIMARY KEY,
    CustomerID INT          NOT NULL REFERENCES Sales.Customer (CustomerID),
    OrderDate  DATETIME2(0) NOT NULL,
    Status     VARCHAR(20)  NOT NULL
);

CREATE TABLE Sales.OrderLines (
    OrderID   INT            NOT NULL REFERENCES Sales.Orders (OrderID),
    [LineNo]  INT            NOT NULL,
    ProductID INT            NOT NULL REFERENCES Sales.Product (ProductID),
    Quantity  INT            NOT NULL,
    UnitPrice DECIMAL(10, 2) NOT NULL,
    Discount  DECIMAL(5, 4)  NOT NULL CONSTRAINT DF_OrderLines_Discount DEFAULT (0),
    CONSTRAINT PK_OrderLines PRIMARY KEY (OrderID, [LineNo])
);
GO

-- 500 customers (every 25th inactive)
WITH n AS (SELECT TOP (500) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS i
           FROM sys.all_objects a CROSS JOIN sys.all_objects b)
INSERT Sales.Customer (CustomerID, FirstName, LastName, Email, City, Country, IsActive, ModifiedDate)
SELECT i,
       CHOOSE(i % 10 + 1, N'Asha', N'Rahul', N'Maria', N'John', N'Wei', N'Priya', N'Omar', N'Sara', N'Liam', N'Yuki'),
       CHOOSE((i / 10) % 10 + 1, N'Patil', N'Sharma', N'Garcia', N'Smith', N'Chen', N'Iyer', N'Khan', N'Lopez', N'Brown', N'Sato'),
       CONCAT('customer', i, '@', CHOOSE(i % 4 + 1, 'contoso.com', 'fabrikam.co.uk', 'northwind.com', 'example.in')),
       CHOOSE(i % 6 + 1, N'Pune', N'Mumbai', N'London', N'New York', N'Singapore', N'Berlin'),
       CHOOSE(i % 6 + 1, N'India', N'India', N'United Kingdom', N'United States', N'Singapore', N'Germany'),
       CASE WHEN i % 25 = 0 THEN 0 ELSE 1 END,
       DATEADD(DAY, -(i % 400), CAST(SYSDATETIME() AS DATETIME2(0)))
FROM n;

-- 40 products
WITH n AS (SELECT TOP (40) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS i FROM sys.all_objects)
INSERT Sales.Product (ProductID, ProductName, Category, ListPrice)
SELECT i,
       CONCAT(CHOOSE(i % 5 + 1, N'Laptop', N'Monitor', N'Keyboard', N'Headset', N'Dock'), N' ', CHAR(64 + (i % 26) + 1), i),
       CHOOSE(i % 5 + 1, N'Computers', N'Displays', N'Accessories', N'Audio', N'Accessories'),
       CAST(10 + (i * 37) % 990 AS DECIMAL(10, 2)) + 0.99
FROM n;

-- 20,000 orders over the last two years; every 50th in the last 7 days (the packages load the last 7 days)
WITH n AS (SELECT TOP (20000) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS i
           FROM sys.all_objects a CROSS JOIN sys.all_objects b)
INSERT Sales.Orders (OrderID, CustomerID, OrderDate, Status)
SELECT 100000 + i,
       (i * 37) % 500 + 1,
       CASE WHEN i % 50 = 0 THEN DATEADD(MINUTE, -(i % (7 * 1440)), CAST(SYSDATETIME() AS DATETIME2(0)))
            ELSE DATEADD(MINUTE, -((i * 53) % (730 * 1440)), CAST(SYSDATETIME() AS DATETIME2(0))) END,
       CASE i % 40 WHEN 0 THEN 'Cancelled' WHEN 1 THEN 'Open' ELSE 'Shipped' END
FROM n;

-- 1 to 4 lines per order
INSERT Sales.OrderLines (OrderID, [LineNo], ProductID, Quantity, UnitPrice, Discount)
SELECT o.OrderID, l.k, p.ProductID, (o.OrderID + l.k) % 5 + 1, p.ListPrice,
       CASE WHEN (o.OrderID + l.k) % 10 = 0 THEN 0.1 ELSE 0 END
FROM Sales.Orders AS o
CROSS JOIN (VALUES (1), (2), (3), (4)) AS l (k)
JOIN Sales.Product AS p ON p.ProductID = ((o.OrderID * 7 + l.k * 13) % 40) + 1
WHERE l.k <= (o.OrderID % 4) + 1;
GO

DECLARE @c INT = (SELECT COUNT(*) FROM Sales.Customer), @o INT = (SELECT COUNT(*) FROM Sales.Orders),
        @l INT = (SELECT COUNT(*) FROM Sales.OrderLines);
PRINT CONCAT('DemoSalesOLTP ready: ', @c, ' customers, ', @o, ' orders, ', @l, ' order lines.');
GO
