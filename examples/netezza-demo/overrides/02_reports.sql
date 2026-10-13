-- Manual fix (WishBridge override): AGE() -> datediff in days.
SELECT CUSTOMER_ID, FULL_NAME, COALESCE(EMAIL, 'n/a') AS EMAIL,
       datediff(DAY, CREATED_AT, current_timestamp()) AS ACCOUNT_AGE_DAYS
FROM workspace.wishbridge_netezza.CUSTOMERS;

SELECT CUSTOMER_ID, SUM(AMOUNT) AS TOTAL
FROM workspace.wishbridge_netezza.ORDERS
WHERE STATUS <> 'Cancelled'
GROUP BY CUSTOMER_ID
ORDER BY TOTAL DESC
LIMIT 3;
