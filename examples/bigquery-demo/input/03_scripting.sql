-- Close orders that have been open for more than 30 days
DECLARE cutoff TIMESTAMP DEFAULT TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY);
UPDATE sales.orders SET status = 'Closed' WHERE status = 'Open' AND order_date < cutoff;
