-- Demo-only stand-in for the Informatica PowerCenter source database (sample data).
-- In a real migration the source is a Lakehouse Federation catalog pointing at the legacy database.
-- Run with:  wishbridge sql setup_demo_source.sql

CREATE SCHEMA IF NOT EXISTS workspace.wishbridge_informatica_src;

CREATE OR REPLACE TABLE workspace.wishbridge_informatica_src.ORDERS (
    ORDER_ID INT NOT NULL,
    CUSTOMER_ID INT NOT NULL,
    ORDER_DATE TIMESTAMP NOT NULL,
    AMOUNT DECIMAL(12,2) NOT NULL,
    STATUS STRING
);

INSERT INTO workspace.wishbridge_informatica_src.ORDERS VALUES
    (101, 1, TIMESTAMP'2026-07-01 10:00:00', 450.00, 'Closed'),
    (102, 1, TIMESTAMP'2026-08-15 12:30:00', 780.50, 'Open'),
    (103, 2, TIMESTAMP'2026-08-20 09:45:00', 120.00, 'Cancelled'),
    (104, 3, TIMESTAMP'2026-09-02 15:10:00', 2300.00, 'Open'),
    (105, 3, TIMESTAMP'2026-09-18 17:20:00', 560.25, 'Closed'),
    (106, 4, TIMESTAMP'2026-09-25 08:05:00', 99.99, 'Open'),
    (107, 5, TIMESTAMP'2026-10-01 13:55:00', 1250.00, ' open '),
    (108, 2, TIMESTAMP'2026-10-03 11:11:00', 310.00, 'Closed');

-- What the legacy Informatica mapping produced from these orders (its output table, exported for comparison):
-- drop cancelled orders, add 18% tax, trim and upper-case the status.
CREATE OR REPLACE TABLE workspace.wishbridge_informatica_src.ORDERS_CLEAN AS
SELECT ORDER_ID, CUSTOMER_ID, ORDER_DATE,
       CAST(ROUND(AMOUNT * 1.18, 2) AS DECIMAL(12,2)) AS AMOUNT_WITH_TAX,
       UPPER(TRIM(STATUS)) AS STATUS
FROM workspace.wishbridge_informatica_src.ORDERS
WHERE STATUS != 'Cancelled';
