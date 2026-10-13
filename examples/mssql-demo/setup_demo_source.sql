-- Demo-only stand-in for the SQL Server source database.
-- In a real migration the source is a Lakehouse Federation catalog that points at SQL Server;
-- here the same tables live in a Databricks schema so the whole pipeline can be shown without one.
-- Run with:  wishbridge sql setup_demo_source.sql

CREATE SCHEMA IF NOT EXISTS workspace.wishbridge_demo_src;

CREATE OR REPLACE TABLE workspace.wishbridge_demo_src.Customers (
    CustomerID INT NOT NULL,
    FullName STRING NOT NULL,
    Email STRING,
    CreatedAt TIMESTAMP
);

CREATE OR REPLACE TABLE workspace.wishbridge_demo_src.Orders (
    OrderID INT NOT NULL,
    CustomerID INT NOT NULL,
    OrderDate TIMESTAMP NOT NULL,
    Amount DECIMAL(19, 4) NOT NULL,
    Status STRING
);

INSERT INTO workspace.wishbridge_demo_src.Customers VALUES
    (1, 'Asha Patil',     'Asha.Patil@Example.com',    TIMESTAMP'2025-01-10 09:15:00'),
    (2, 'Rahul Mehta',    'rahul.mehta@example.com',   TIMESTAMP'2025-02-03 14:02:00'),
    (3, 'Neha Kulkarni',  'NEHA.K@example.com',        TIMESTAMP'2025-03-21 11:40:00'),
    (4, 'Vikram Joshi',   'vikram.joshi@example.com',  TIMESTAMP'2025-05-08 16:25:00'),
    (5, 'Priya Deshmukh', 'priya.d@example.com',       TIMESTAMP'2025-07-30 10:05:00');

INSERT INTO workspace.wishbridge_demo_src.Orders VALUES
    (101, 1, TIMESTAMP'2026-07-01 10:00:00',  450.0000, 'Closed'),
    (102, 1, TIMESTAMP'2026-08-15 12:30:00',  780.5000, 'Open'),
    (103, 2, TIMESTAMP'2026-08-20 09:45:00',  120.0000, 'Cancelled'),
    (104, 3, TIMESTAMP'2026-09-02 15:10:00', 2300.0000, 'Open'),
    (105, 3, TIMESTAMP'2026-09-18 17:20:00',  560.2500, 'Closed'),
    (106, 4, TIMESTAMP'2026-09-25 08:05:00',   99.9900, 'Open'),
    (107, 5, TIMESTAMP'2026-10-01 13:55:00', 1250.0000, 'Open'),
    (108, 2, TIMESTAMP'2026-10-03 11:11:00',  310.0000, 'Closed');
