-- Manual fix (WishBridge override): UNLOAD -> INSERT OVERWRITE DIRECTORY; VACUUM -> OPTIMIZE.
UPDATE workspace.wishbridge_redshift.orders
SET status = 'Closed'
WHERE status = 'Open' AND order_date < current_timestamp() - INTERVAL 30 DAYS;

-- Export (enable once an external location for s3://acme-exports exists):
--   INSERT OVERWRITE DIRECTORY 's3://acme-exports/orders/' USING PARQUET
--   SELECT * FROM workspace.wishbridge_redshift.orders

OPTIMIZE workspace.wishbridge_redshift.orders;

ANALYZE TABLE workspace.wishbridge_redshift.orders COMPUTE STATISTICS;
