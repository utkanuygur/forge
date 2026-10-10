# Changelog

## Unreleased

- Moved price calculation out of the request handlers into `acme.pricing`.

## 0.3.0 - 2026-08-21

- Added `POST /orders/{order_id}/ship` to mark paid orders as shipped.
- `GET /orders/{order_id}` now returns 404 for unknown IDs instead of 500.

## 0.2.0 - 2026-07-30

- Added `POST /orders/{order_id}/pay`.
- `POST /orders` accepts an optional `discount_percent`.

## 0.1.0 - 2026-07-02

- Added `POST /orders`, `GET /orders` and `GET /orders/{order_id}`.
- Added `GET /health`.
