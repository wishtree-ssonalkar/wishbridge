-- Manual fix (WishBridge override): PARTITION BY DATE(order_date) -> CLUSTER BY (Delta partitions by columns only).
CREATE TABLE workspace.wishbridge_bigquery.customers (
    customer_id BIGINT NOT NULL,
    full_name STRING NOT NULL,
    email STRING,
    created_at TIMESTAMP
);

CREATE TABLE workspace.wishbridge_bigquery.orders (
    order_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    order_date TIMESTAMP NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    status STRING
)
CLUSTER BY (customer_id, order_date);
