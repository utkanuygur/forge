from _util import changelog, edit

edit("src/acme/pricing.py", '''class Quote:
    subtotal_cents: int
    discount_cents: int
    total_cents: int
''', '''class Quote:
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    total_cents: int


FREE_SHIPPING_MIN_CENTS = 5000
SHIPPING_FEE_CENTS = 499
''')
edit("src/acme/pricing.py", '''    discount = discount_cents(subtotal, discount_percent)
    return Quote(subtotal_cents=subtotal, discount_cents=discount, total_cents=subtotal - discount)
''', '''    discount = discount_cents(subtotal, discount_percent)
    shipping = shipping_cents(subtotal - discount)
    return Quote(
        subtotal_cents=subtotal,
        discount_cents=discount,
        shipping_cents=shipping,
        total_cents=subtotal - discount + shipping,
    )


def shipping_cents(discounted_subtotal: int) -> int:
    return 0 if discounted_subtotal >= FREE_SHIPPING_MIN_CENTS else SHIPPING_FEE_CENTS
''')
edit("src/acme/models.py", "    discount_cents: int\n    total_cents: int\n",
     "    discount_cents: int\n    shipping_cents: int\n    total_cents: int\n")
edit("src/acme/app.py", "            discount_cents=price.discount_cents,\n",
     "            discount_cents=price.discount_cents,\n            shipping_cents=price.shipping_cents,\n")
# existing expectations now include shipping
edit("tests/test_orders.py", '''    assert order["discount_cents"] == 0
    assert order["total_cents"] == 4499
''', '''    assert order["discount_cents"] == 0
    assert order["shipping_cents"] == 499
    assert order["total_cents"] == 4998


def test_free_shipping_from_fifty_dollars(make_order) -> None:
    order = make_order(items=[{"sku": "LAMP", "quantity": 1, "unit_price_cents": 5000}])
    assert order["shipping_cents"] == 0
    assert order["total_cents"] == 5000
''')
edit("tests/test_orders.py", '''    assert order["discount_cents"] == 500
    assert order["total_cents"] == 2000
''', '''    assert order["discount_cents"] == 500
    assert order["total_cents"] == 2499
''')
edit("tests/test_pricing.py",
     "    assert (result.subtotal_cents, result.discount_cents, result.total_cents) == (2000, 500, 1500)\n",
     "    assert (result.subtotal_cents, result.discount_cents) == (2000, 500)\n"
     "    assert (result.shipping_cents, result.total_cents) == (499, 1999)\n")
changelog("Orders under $50.00 now include a $4.99 `shipping_cents` fee in `total_cents`.")
