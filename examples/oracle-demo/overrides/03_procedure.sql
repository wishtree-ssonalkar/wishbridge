-- Manual fix (WishBridge override): PL/SQL procedure rewritten as a Databricks SQL procedure.
-- DBMS_OUTPUT + SQL%ROWCOUNT -> the count is returned as a result; COMMIT dropped (each statement is atomic).
CREATE OR REPLACE PROCEDURE workspace.wishbridge_oracle.CLOSE_STALE_ORDERS(IN p_days INT)
    LANGUAGE SQL
    SQL SECURITY INVOKER
AS
BEGIN
    DECLARE closed INT DEFAULT 0;
    SET closed = (SELECT COUNT(*) FROM workspace.wishbridge_oracle.ORDERS
                  WHERE STATUS = 'Open' AND ORDER_DATE < date_sub(current_date(), p_days));
    UPDATE workspace.wishbridge_oracle.ORDERS
       SET STATUS = 'Closed'
     WHERE STATUS = 'Open' AND ORDER_DATE < date_sub(current_date(), p_days);
    SELECT CONCAT('Closed ', closed, ' orders') AS message;
END;
