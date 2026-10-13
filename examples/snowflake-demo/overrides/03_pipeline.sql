-- Manual fix (WishBridge override): Snowflake stream, task and stage load.
-- STREAM -> Change Data Feed on the table; read the changes with table_changes().
ALTER TABLE workspace.wishbridge_snowflake.ORDERS SET TBLPROPERTIES (delta.enableChangeDataFeed = true);

-- TASK -> this statement runs as a Databricks Job task, scheduled daily at 02:00 UTC.
UPDATE workspace.wishbridge_snowflake.ORDERS
SET STATUS = 'Closed'
WHERE STATUS = 'Open' AND ORDER_DATE < CURRENT_TIMESTAMP() - INTERVAL 30 DAYS;

-- Stage load -> COPY INTO from a Unity Catalog volume (enable once the landing volume exists):
--   COPY INTO workspace.wishbridge_snowflake.ORDERS FROM '/Volumes/workspace/landing/raw/orders/'
--   FILEFORMAT = CSV FORMAT_OPTIONS ('header' = 'true')
