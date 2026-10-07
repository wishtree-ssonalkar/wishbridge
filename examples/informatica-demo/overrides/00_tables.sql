-- Hand-written (WishBridge override): target tables for the migrated Informatica mapping.
-- ETL exports carry no DDL; ORDERS is the mapping's input, ORDERS_CLEAN its output.
CREATE TABLE workspace.wishbridge_informatica.ORDERS (
    ORDER_ID INT NOT NULL,
    CUSTOMER_ID INT NOT NULL,
    ORDER_DATE TIMESTAMP NOT NULL,
    AMOUNT DECIMAL(12,2) NOT NULL,
    STATUS STRING
);

CREATE TABLE workspace.wishbridge_informatica.ORDERS_CLEAN (
    ORDER_ID INT NOT NULL,
    CUSTOMER_ID INT NOT NULL,
    ORDER_DATE TIMESTAMP NOT NULL,
    AMOUNT_WITH_TAX DECIMAL(12,2) NOT NULL,
    STATUS STRING
);
