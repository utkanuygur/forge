from acme.models import LineItem
from acme.pricing import discount_cents, format_cents, quote, subtotal_cents


def test_subtotal() -> None:
    items = [
        LineItem(sku="A", quantity=3, unit_price_cents=199),
        LineItem(sku="B", quantity=1, unit_price_cents=1000),
    ]
    assert subtotal_cents(items) == 1597


def test_discount() -> None:
    assert discount_cents(2000, 10) == 200
    assert discount_cents(2000, 0) == 0
    assert discount_cents(2000, 100) == 2000


def test_quote() -> None:
    result = quote([LineItem(sku="A", quantity=4, unit_price_cents=500)], discount_percent=25)
    assert (result.subtotal_cents, result.discount_cents, result.total_cents) == (2000, 500, 1500)


def test_format_cents() -> None:
    assert format_cents(0) == "$0.00"
    assert format_cents(123456) == "$1,234.56"
    assert format_cents(-5) == "-$0.05"
