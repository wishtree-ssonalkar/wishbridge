-- Demo-only stand-in for the Teradata source database (sample data).
-- In a real migration the source is a Lakehouse Federation catalog pointing at the legacy database.
-- Run with:  wishbridge sql setup_demo_source.sql

CREATE SCHEMA IF NOT EXISTS workspace.wishbridge_teradata_src;

CREATE OR REPLACE TABLE workspace.wishbridge_teradata_src.CUSTOMERS (
    CUSTOMER_ID INT NOT NULL,
    FULL_NAME STRING NOT NULL,
    EMAIL STRING,
    CREATED_AT TIMESTAMP
);

INSERT INTO workspace.wishbridge_teradata_src.CUSTOMERS VALUES
    (1, 'Asha Patil', 'Asha.Patil@Example.com', TIMESTAMP'2025-01-10 09:15:00'),
    (2, 'Rahul Mehta', 'rahul.mehta@example.com', TIMESTAMP'2025-02-03 14:02:00'),
    (3, 'Neha Kulkarni', 'NEHA.K@example.com', TIMESTAMP'2025-03-21 11:40:00'),
    (4, 'Vikram Joshi', NULL, TIMESTAMP'2025-05-08 16:25:00'),
    (5, 'Priya Deshmukh', 'priya.d@example.com', TIMESTAMP'2025-07-30 10:05:00');

CREATE OR REPLACE TABLE workspace.wishbridge_teradata_src.ORDERS (
    ORDER_ID INT NOT NULL,
    CUSTOMER_ID INT NOT NULL,
    ORDER_DATE TIMESTAMP NOT NULL,
    AMOUNT DECIMAL(12,2) NOT NULL,
    STATUS STRING
);

INSERT INTO workspace.wishbridge_teradata_src.ORDERS VALUES
    (101, 1, TIMESTAMP'2026-07-01 10:00:00', 450.00, 'Closed'),
    (102, 1, TIMESTAMP'2026-08-15 12:30:00', 780.50, 'Open'),
    (103, 2, TIMESTAMP'2026-08-20 09:45:00', 120.00, 'Cancelled'),
    (104, 3, TIMESTAMP'2026-09-02 15:10:00', 2300.00, 'Open'),
    (105, 3, TIMESTAMP'2026-09-18 17:20:00', 560.25, 'Closed'),
    (106, 4, TIMESTAMP'2026-09-25 08:05:00', 99.99, 'Open'),
    (107, 5, TIMESTAMP'2026-10-01 13:55:00', 1250.00, ' open '),
    (108, 2, TIMESTAMP'2026-10-03 11:11:00', 310.00, 'Closed');
