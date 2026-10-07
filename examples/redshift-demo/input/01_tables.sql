CREATE TABLE sales.customers (
    customer_id INTEGER NOT NULL ENCODE az64,
    full_name VARCHAR(200) NOT NULL,
    email VARCHAR(255),
    created_at TIMESTAMP
) DISTSTYLE ALL SORTKEY (customer_id);

CREATE TABLE sales.orders (
    order_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    order_date TIMESTAMP NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    status VARCHAR(20)
) DISTKEY (customer_id) SORTKEY (order_date);
