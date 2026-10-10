"""Order pricing. All amounts are in cents."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from acme.models import LineItem


@dataclass(frozen=True)
class Quote:
    subtotal_cents: int
    discount_cents: int
    total_cents: int


def subtotal_cents(items: Iterable[LineItem]) -> int:
    return sum(item.unit_price_cents * item.quantity for item in items)


def discount_cents(subtotal: int, percent: int) -> int:
    return subtotal * percent // 100


def quote(items: Iterable[LineItem], discount_percent: int = 0) -> Quote:
    subtotal = subtotal_cents(items)
    discount = discount_cents(subtotal, discount_percent)
    return Quote(subtotal_cents=subtotal, discount_cents=discount, total_cents=subtotal - discount)


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"
