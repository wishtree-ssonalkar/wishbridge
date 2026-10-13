-- Manual fix (WishBridge override): BTEQ batch rewritten as SQL for a Databricks Job task.
-- .LOGON/.LOGOFF/.QUIT dropped (the job runs with its own identity); .IF ERRORCODE -> the task fails on error.
-- The volatile table is replaced by the WHERE clause; CURRENT_TIMESTAMP - 30 -> INTERVAL 30 DAYS.
UPDATE workspace.wishbridge_teradata.ORDERS
SET STATUS = 'Closed'
WHERE STATUS = 'Open' AND ORDER_DATE < CURRENT_TIMESTAMP() - INTERVAL 30 DAYS;
