# Correct totals, CHANGELOG and lint fine, but coupon values are float dollars and percents.
from _util import changelog, edit

edit("src/acme/models.py", "    discount_percent: int = Field(default=0, ge=0, le=100)\n", '''\
    discount_percent: int = Field(default=0, ge=0, le=100)
    coupon_code: str | None = None
''')
edit("src/acme/models.py", "    discount_percent: int\n    subtotal_cents: int\n",
     "    discount_percent: int\n    coupon_code: str | None = None\n    subtotal_cents: int\n")
edit("src/acme/app.py", "from acme.store import OrderNotFound, OrderStore\n", '''from acme.store import OrderNotFound, OrderStore

# code -> (kind, value, minimum order in dollars)
COUPONS: dict[str, tuple[str, float, float]] = {
    "WELCOME10": ("percent", 0.10, 0.0),
    "FIVEOFF": ("amount", 5.00, 25.00),
}
''')
edit("src/acme/app.py", '''        price = quote(body.items, body.discount_percent)
''', '''        price = quote(body.items, body.discount_percent)
        code = body.coupon_code.upper() if body.coupon_code else None
        discount = price.discount_cents
        if code is not None:
            if code not in COUPONS or body.discount_percent:
                raise HTTPException(status_code=422, detail="invalid coupon")
            kind, value, minimum = COUPONS[code]
            subtotal = price.subtotal_cents / 100
            if subtotal < minimum:
                raise HTTPException(status_code=422, detail="order too small for coupon")
            off = subtotal * value if kind == "percent" else value
            discount = round(off * 100)
''')
edit("src/acme/app.py", '''            discount_percent=body.discount_percent,
            subtotal_cents=price.subtotal_cents,
            discount_cents=price.discount_cents,
            total_cents=price.total_cents,
''', '''            discount_percent=body.discount_percent,
            coupon_code=code,
            subtotal_cents=price.subtotal_cents,
            discount_cents=discount,
            total_cents=price.subtotal_cents - discount,
''')
changelog("Added coupon codes to `POST /orders`.")
