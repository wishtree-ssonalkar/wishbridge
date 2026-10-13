-- Hand-written (WishBridge override): the warehouse tables the SSIS packages load.
-- SSIS projects carry no table DDL; these come from the SalesDW database project.
CREATE TABLE workspace.wishbridge_ssis.Customer (      -- stg.Customer (staging)
    CustomerID INT NOT NULL,
    FullName STRING,
    Email STRING,
    EmailDomain STRING,
    City STRING,
    Country STRING,
    SourceModifiedDate TIMESTAMP,
    LoadDate TIMESTAMP
);

CREATE TABLE workspace.wishbridge_ssis.DimCustomer (   -- dw.DimCustomer
    CustomerKey INT GENERATED ALWAYS AS (CustomerID + 1000),  -- IDENTITY on SQL Server; repeatable keys for the demo
    CustomerID INT NOT NULL,
    FullName STRING,
    Email STRING,
    EmailDomain STRING,
    City STRING,
    Country STRING,
    CreatedDate TIMESTAMP,
    UpdatedDate TIMESTAMP
);

CREATE TABLE workspace.wishbridge_ssis.FactDailySales (   -- dw.FactDailySales
    OrderDate DATE NOT NULL,
    CustomerKey INT NOT NULL,
    OrderCount BIGINT,
    TotalQuantity BIGINT,
    TotalAmount DECIMAL(18,4)
);
