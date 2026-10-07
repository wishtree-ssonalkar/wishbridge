-- Customers with days since sign-up
SELECT customer_id, full_name, IFNULL(email, 'n/a') AS email,
       TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), created_at, DAY) AS days_since_signup
FROM sales.customers;

-- Orders per customer as an array
SELECT customer_id, ARRAY_AGG(order_id ORDER BY order_date) AS order_ids, SUM(amount) AS total
FROM sales.orders
WHERE status != 'Cancelled'
GROUP BY customer_id;

-- Everything except the e-mail address
SELECT * EXCEPT (email) FROM sales.customers;
