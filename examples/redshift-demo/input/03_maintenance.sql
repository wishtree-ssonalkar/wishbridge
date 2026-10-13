-- Nightly clean-up, export and housekeeping
UPDATE sales.orders SET status = 'Closed'
WHERE status = 'Open' AND order_date < DATEADD(day, -30, GETDATE());

UNLOAD ('SELECT * FROM sales.orders')
TO 's3://acme-exports/orders_'
IAM_ROLE 'arn:aws:iam::123456789012:role/unload'
PARQUET;

VACUUM sales.orders;
ANALYZE sales.orders;
