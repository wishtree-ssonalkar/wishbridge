-- Demo-only stand-in for the SSIS source database SalesOLTP (sample data) and for the output the legacy
-- SSIS load produced. In a real migration Sales.* is a Lakehouse Federation catalog over the source database.
-- Order dates are relative to today because the package loads the last 7 days.
-- Run with:  wishbridge sql setup_demo_source.sql

CREATE SCHEMA IF NOT EXISTS workspace.wishbridge_ssis_src;

CREATE OR REPLACE TABLE workspace.wishbridge_ssis_src.Customer (
    CustomerID INT NOT NULL, FirstName STRING, LastName STRING, Email STRING,
    City STRING, Country STRING, ModifiedDate TIMESTAMP, IsActive BOOLEAN);

INSERT INTO workspace.wishbridge_ssis_src.Customer VALUES
    (1, 'Asha ', 'Patil', 'asha.patil@Contoso.com', 'Pune', 'India', TIMESTAMP'2026-01-10 09:00:00', true),
    (2, 'Rahul', ' Mehta', 'rahul@fabrikam.co.uk', 'London', 'United Kingdom', TIMESTAMP'2026-02-11 10:00:00', true),
    (3, 'Maria', 'Garcia', 'maria.garcia@contoso.com', 'Madrid', 'Spain', TIMESTAMP'2026-03-12 11:00:00', true),
    (4, 'John', 'Smith', 'john@northwind.com', 'Seattle', 'USA', TIMESTAMP'2026-04-13 12:00:00', false),
    (5, 'Li', 'Wei', 'li.wei@adventure-works.cn', 'Shanghai', 'China', TIMESTAMP'2026-05-14 13:00:00', true);

CREATE OR REPLACE TABLE workspace.wishbridge_ssis_src.Orders (
    OrderID INT NOT NULL, CustomerID INT NOT NULL, OrderDate TIMESTAMP NOT NULL, Status STRING);

INSERT INTO workspace.wishbridge_ssis_src.Orders VALUES
    (1001, 1, CAST(current_date() - 1 AS TIMESTAMP) + INTERVAL 9 HOURS, 'Shipped'),
    (1002, 1, CAST(current_date() - 1 AS TIMESTAMP) + INTERVAL 15 HOURS, 'Shipped'),
    (1003, 2, CAST(current_date() - 2 AS TIMESTAMP) + INTERVAL 11 HOURS, 'Open'),
    (1004, 3, CAST(current_date() - 3 AS TIMESTAMP) + INTERVAL 10 HOURS, 'Cancelled'),
    (1005, 3, CAST(current_date() - 3 AS TIMESTAMP) + INTERVAL 16 HOURS, 'Shipped'),
    (1006, 4, CAST(current_date() - 4 AS TIMESTAMP) + INTERVAL 8 HOURS, 'Shipped'),
    (1007, 5, CAST(current_date() - 6 AS TIMESTAMP) + INTERVAL 14 HOURS, 'Open'),
    (1008, 2, CAST(current_date() - 12 AS TIMESTAMP) + INTERVAL 9 HOURS, 'Shipped');

CREATE OR REPLACE TABLE workspace.wishbridge_ssis_src.OrderLines (
    OrderID INT NOT NULL, LineNo INT NOT NULL, ProductID INT NOT NULL,
    Quantity INT NOT NULL, UnitPrice DECIMAL(12,2) NOT NULL, Discount DECIMAL(5,4) NOT NULL);

INSERT INTO workspace.wishbridge_ssis_src.OrderLines VALUES
    (1001, 1, 501, 2, 120.00, 0.0000), (1001, 2, 502, 1, 80.50, 0.1000),
    (1002, 1, 503, 5, 15.25, 0.0000),
    (1003, 1, 501, 1, 120.00, 0.0500), (1003, 2, 504, 3, 42.00, 0.0000),
    (1004, 1, 505, 10, 9.99, 0.0000),
    (1005, 1, 502, 4, 80.50, 0.1500),
    (1006, 1, 501, 1, 120.00, 0.0000),
    (1007, 1, 506, 7, 33.30, 0.0200), (1007, 2, 503, 2, 15.25, 0.0000),
    (1008, 1, 501, 9, 120.00, 0.0000);

-- What the legacy SSIS load produced (exported for comparison). DimCustomer.CustomerKey is CustomerID + 1000
-- in this demo so keys are repeatable; audit dates are left out of the comparison.
CREATE OR REPLACE TABLE workspace.wishbridge_ssis_src.DimCustomer_legacy AS
SELECT CustomerID + 1000 AS CustomerKey, CustomerID,
       TRIM(FirstName) || ' ' || TRIM(LastName) AS FullName,
       Email, LOWER(SUBSTRING(Email, instr(Email, '@') + 1, length(Email))) AS EmailDomain, City, Country
FROM workspace.wishbridge_ssis_src.Customer WHERE IsActive;

CREATE OR REPLACE TABLE workspace.wishbridge_ssis_src.FactDailySales_legacy AS
SELECT CAST(o.OrderDate AS DATE) AS OrderDate, d.CustomerKey,
       COUNT(DISTINCT o.OrderID) AS OrderCount, SUM(ol.Quantity) AS TotalQuantity,
       CAST(SUM(ol.Quantity * ol.UnitPrice * (1 - ol.Discount)) AS DECIMAL(18,4)) AS TotalAmount
FROM workspace.wishbridge_ssis_src.Orders o
JOIN workspace.wishbridge_ssis_src.OrderLines ol ON ol.OrderID = o.OrderID
JOIN workspace.wishbridge_ssis_src.DimCustomer_legacy d ON d.CustomerID = o.CustomerID
WHERE o.OrderDate >= dateadd(DAY, -7, current_date()) AND o.Status <> 'Cancelled'
GROUP BY CAST(o.OrderDate AS DATE), d.CustomerKey;
