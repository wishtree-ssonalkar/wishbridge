-- Customers with days since sign-up
SELECT customer_id, full_name, NVL(email, 'n/a') AS email,
       DATEDIFF(day, created_at, GETDATE()) AS days_since_signup
FROM sales.customers;

-- Orders per customer as a list
SELECT customer_id,
       LISTAGG(order_id::VARCHAR, ',') WITHIN GROUP (ORDER BY order_id) AS order_ids,
       SUM(amount) AS total
FROM sales.orders
WHERE status <> 'Cancelled'
GROUP BY customer_id;
