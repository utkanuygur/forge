"""HTTP API for the order service."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException

from acme import __version__
from acme.log import get_logger
from acme.models import Order, OrderCreate, OrderStatus
from acme.pricing import format_cents, quote
from acme.store import OrderNotFound, OrderStore

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def create_app(store: OrderStore | None = None) -> FastAPI:
    app = FastAPI(title="Acme Orders", version=__version__)
    orders = store if store is not None else OrderStore()

    def load(order_id: str) -> Order:
        try:
            return orders.get(order_id)
        except OrderNotFound:
            raise HTTPException(status_code=404, detail=f"order {order_id} not found") from None

    def transition(order: Order, expected: OrderStatus, new: OrderStatus) -> Order:
        if order.status != expected:
            raise HTTPException(
                status_code=409,
                detail=f"order {order.id} is {order.status}, expected {expected}",
            )
        updated = order.model_copy(update={"status": new, "updated_at": _now()})
        orders.save(updated)
        logger.info("order %s %s -> %s", order.id, expected, new)
        return updated

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/orders", status_code=201)
    def create_order(body: OrderCreate) -> Order:
        price = quote(body.items, body.discount_percent)
        now = _now()
        order = Order(
            id=orders.next_id(),
            customer_email=body.customer_email,
            items=body.items,
            status="pending",
            discount_percent=body.discount_percent,
            subtotal_cents=price.subtotal_cents,
            discount_cents=price.discount_cents,
            total_cents=price.total_cents,
            created_at=now,
            updated_at=now,
        )
        orders.add(order)
        logger.info("order %s created total=%s", order.id, format_cents(order.total_cents))
        return order

    @app.get("/orders")
    def list_orders() -> list[Order]:
        return orders.list()

    @app.get("/orders/{order_id}")
    def get_order(order_id: str) -> Order:
        return load(order_id)

    @app.post("/orders/{order_id}/pay")
    def pay_order(order_id: str) -> Order:
        return transition(load(order_id), "pending", "paid")

    @app.post("/orders/{order_id}/ship")
    def ship_order(order_id: str) -> Order:
        return transition(load(order_id), "paid", "shipped")

    return app


app = create_app()
