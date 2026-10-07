-- Manual fix (WishBridge override): ROWNUM -> LIMIT, DECODE/SIGN -> CASE, Oracle date mask -> Java pattern,
-- old-style (+) outer join -> LEFT JOIN.
SELECT CUSTOMER_ID, FULL_NAME, COALESCE(EMAIL, 'n/a') AS EMAIL,
       CASE WHEN CUSTOMER_ID > 3 THEN 'NEW' ELSE 'EARLY' END AS COHORT,
       DATE_FORMAT(CREATED_AT, 'yyyy-MM-dd') AS CREATED
FROM workspace.wishbridge_oracle.CUSTOMERS
LIMIT 10;

SELECT c.FULL_NAME, o.ORDER_ID, o.AMOUNT
FROM workspace.wishbridge_oracle.CUSTOMERS c
LEFT JOIN workspace.wishbridge_oracle.ORDERS o ON c.CUSTOMER_ID = o.CUSTOMER_ID
ORDER BY c.FULL_NAME;
