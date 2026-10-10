# acme-orders

The order service behind the Acme storefront. It accepts orders from the checkout flow,
prices them, and tracks them through payment and fulfillment.

## Running locally

```
make run      # http://localhost:8000, docs at /docs
make test
```

Dependencies are listed in `pyproject.toml`. Storage is in memory for now; the Postgres
backed store is tracked separately.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Liveness probe |
| POST | `/orders` | Create an order from line items |
| GET | `/orders` | List orders, oldest first |
| GET | `/orders/{order_id}` | Fetch one order |
| POST | `/orders/{order_id}/pay` | Mark a pending order as paid |
| POST | `/orders/{order_id}/ship` | Mark a paid order as shipped |

Order lifecycle: `pending` -> `paid` -> `shipped`.
