CREATE TABLE sales.customers (
  customer_id INT64 NOT NULL,
  full_name STRING NOT NULL,
  email STRING,
  created_at TIMESTAMP
);

CREATE TABLE sales.orders (
  order_id INT64 NOT NULL,
  customer_id INT64 NOT NULL,
  order_date TIMESTAMP NOT NULL,
  amount NUMERIC(12, 2) NOT NULL,
  status STRING
)
PARTITION BY DATE(order_date)
CLUSTER BY customer_id;
