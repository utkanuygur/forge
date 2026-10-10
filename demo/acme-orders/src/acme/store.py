"""In-memory order storage."""

from __future__ import annotations

import itertools
import threading

from acme.models import Order


class OrderNotFound(KeyError):
    pass


class OrderStore:
    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def next_id(self) -> str:
        with self._lock:
            return f"ord_{next(self._ids):04d}"

    def add(self, order: Order) -> Order:
        with self._lock:
            self._orders[order.id] = order
        return order

    def get(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise OrderNotFound(order_id) from None

    def save(self, order: Order) -> Order:
        with self._lock:
            if order.id not in self._orders:
                raise OrderNotFound(order.id)
            self._orders[order.id] = order
        return order

    def list(self) -> list[Order]:
        return sorted(self._orders.values(), key=lambda order: order.id)
