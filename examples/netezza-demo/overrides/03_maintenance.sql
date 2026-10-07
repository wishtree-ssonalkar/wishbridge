-- Manual fix (WishBridge override): GROOM -> OPTIMIZE; GENERATE STATISTICS (dropped by the converter) -> ANALYZE.
UPDATE workspace.wishbridge_netezza.ORDERS
SET STATUS = 'Closed'
WHERE STATUS = 'Open' AND ORDER_DATE < current_timestamp() - INTERVAL 30 DAYS;

OPTIMIZE workspace.wishbridge_netezza.ORDERS;

ANALYZE TABLE workspace.wishbridge_netezza.ORDERS COMPUTE STATISTICS;
