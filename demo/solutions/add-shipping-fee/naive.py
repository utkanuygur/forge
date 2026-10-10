# Correct totals, CHANGELOG and lint all fine, but the fee is exposed as float dollars.
from _util import changelog, edit

edit("src/acme/pricing.py", "    total_cents: int\n\n\n", '''    total_cents: int


SHIPPING_FEE = 4.99
FREE_SHIPPING_MIN = 50.00


def shipping_fee(total_cents: int) -> float:
    return 0.0 if total_cents / 100 >= FREE_SHIPPING_MIN else SHIPPING_FEE


''')
edit("src/acme/models.py", "    discount_cents: int\n    total_cents: int\n",
     "    discount_cents: int\n    shipping_fee: float\n    total_cents: int\n")
edit("src/acme/app.py", "from acme.pricing import format_cents, quote",
     "from acme.pricing import format_cents, quote, shipping_fee")
edit("src/acme/app.py", '''        price = quote(body.items, body.discount_percent)
''', '''        price = quote(body.items, body.discount_percent)
        shipping = shipping_fee(price.total_cents)
''')
edit("src/acme/app.py", '''            total_cents=price.total_cents,
''', '''            shipping_fee=shipping,
            total_cents=price.total_cents + round(shipping * 100),
''')
edit("tests/test_orders.py", '''    assert order["discount_cents"] == 0
    assert order["total_cents"] == 4499
''', '''    assert order["discount_cents"] == 0
    assert order["shipping_fee"] == 4.99
    assert order["total_cents"] == 4998
''')
edit("tests/test_orders.py", '''    assert order["total_cents"] == 2000
''', '''    assert order["total_cents"] == 2499
''')
changelog("Orders under $50 now pay a $4.99 shipping fee (`shipping_fee`).")
