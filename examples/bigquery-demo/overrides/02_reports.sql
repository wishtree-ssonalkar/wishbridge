-- Manual fix (WishBridge override): the converter failed on this file and copied it unchanged.
-- TIMESTAMP_DIFF -> datediff; ARRAY_AGG(... ORDER BY ...) -> sorted collect_list; SELECT * EXCEPT works as is.
SELECT customer_id, full_name, COALESCE(email, 'n/a') AS email,
       datediff(DAY, created_at, current_timestamp()) AS days_since_signup
FROM workspace.wishbridge_bigquery.customers;

SELECT customer_id,
       transform(array_sort(collect_list(struct(order_date, order_id))), x -> x.order_id) AS order_ids,
       SUM(amount) AS total
FROM workspace.wishbridge_bigquery.orders
WHERE status != 'Cancelled'
GROUP BY customer_id;

SELECT * EXCEPT (email) FROM workspace.wishbridge_bigquery.customers;
