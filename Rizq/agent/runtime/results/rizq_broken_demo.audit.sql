-- restore 1 missing sale movement(s)
INSERT INTO stock_transactions (id, product_id, transaction_type, quantity, notes) VALUES (?,?, 'sale', ?, ?);  -- params=["st_rizq_agent_1", "prod_0clc3sy", -1, "Rizq Agent: missing sale movement restored"]
-- deduct 1 from stock
UPDATE products SET stock = stock - ? WHERE id=?;  -- params=[1, "prod_0clc3sy"]
-- recompute customer totals from orders
UPDATE customers SET orders_count=?, total_spent=? WHERE phone=?;  -- params=[1, 77.63, "0593520868"]
