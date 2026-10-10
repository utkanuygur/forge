# Store data export (2026-01-01 to 2026-09-30)

| File | Rows | Columns |
|---|---|---|
| `customers.csv` | one per account | customer_id, name, email, region, signup_date (YYYY-MM-DD) |
| `products.csv` | one per product | product_id, name, category, unit_price (list price, USD) |
| `orders.csv` | one per order line | order_id, customer_id, order_date (YYYY-MM-DD), product_id, quantity, unit_price (USD actually charged per unit), status |

`status` is `completed`, `refunded` or `cancelled`. Revenue means quantity x unit_price of completed
order lines. A customer belongs to the region on their customer record. Accounts are exported as they
are in the production database, without any cleanup.
