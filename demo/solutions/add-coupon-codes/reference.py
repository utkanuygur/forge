from _util import append, changelog, edit

edit("src/acme/models.py", "    discount_percent: int = Field(default=0, ge=0, le=100)\n", '''\
    discount_percent: int = Field(default=0, ge=0, le=100)
    coupon_code: str | None = Field(default=None, max_length=32)
''')
edit("src/acme/models.py", "    discount_percent: int\n    subtotal_cents: int\n",
     "    discount_percent: int\n    coupon_code: str | None = None\n    subtotal_cents: int\n")
edit("src/acme/pricing.py", "def subtotal_cents(", '''class InvalidCoupon(ValueError):
    pass


@dataclass(frozen=True)
class Coupon:
    code: str
    percent: int = 0
    amount_cents: int = 0
    min_subtotal_cents: int = 0


COUPONS = {
    coupon.code: coupon
    for coupon in (
        Coupon("WELCOME10", percent=10),
        Coupon("FIVEOFF", amount_cents=500, min_subtotal_cents=2500),
    )
}


def find_coupon(code: str) -> Coupon:
    coupon = COUPONS.get(code.strip().upper())
    if coupon is None:
        raise InvalidCoupon(f"unknown coupon code {code!r}")
    return coupon


def coupon_discount_cents(coupon: Coupon, subtotal: int) -> int:
    if subtotal < coupon.min_subtotal_cents:
        raise InvalidCoupon(
            f"{coupon.code} needs a subtotal of at least {format_cents(coupon.min_subtotal_cents)}"
        )
    return min(subtotal, discount_cents(subtotal, coupon.percent) + coupon.amount_cents)


def subtotal_cents(''')
edit("src/acme/pricing.py", '''def quote(items: Iterable[LineItem], discount_percent: int = 0) -> Quote:
    subtotal = subtotal_cents(items)
    discount = discount_cents(subtotal, discount_percent)
''', '''def quote(
    items: Iterable[LineItem], discount_percent: int = 0, coupon: Coupon | None = None
) -> Quote:
    subtotal = subtotal_cents(items)
    discount = discount_cents(subtotal, discount_percent)
    if coupon is not None:
        if discount_percent:
            raise InvalidCoupon("coupons cannot be combined with discount_percent")
        discount = coupon_discount_cents(coupon, subtotal)
''')
edit("src/acme/app.py", "from acme.pricing import format_cents, quote",
     "from acme.pricing import InvalidCoupon, find_coupon, format_cents, quote")
edit("src/acme/app.py", '''        price = quote(body.items, body.discount_percent)
''', '''        try:
            coupon = find_coupon(body.coupon_code) if body.coupon_code else None
            price = quote(body.items, body.discount_percent, coupon)
        except InvalidCoupon as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
''')
edit("src/acme/app.py", '''            discount_percent=body.discount_percent,
''', '''            discount_percent=body.discount_percent,
            coupon_code=coupon.code if coupon else None,
''')
append("tests/test_orders.py", '''

def test_coupon_codes(make_order, client: TestClient) -> None:
    items = [{"sku": "LAMP", "quantity": 1, "unit_price_cents": 3000}]
    assert make_order(items=items, coupon_code="welcome10")["total_cents"] == 2700
    order = make_order(items=items, coupon_code="FIVEOFF")
    assert (order["discount_cents"], order["coupon_code"]) == (500, "FIVEOFF")
    cheap = [{"sku": "PEN", "quantity": 1, "unit_price_cents": 2000}]
    body = {"customer_email": "ada@example.com", "items": cheap, "coupon_code": "FIVEOFF"}
    assert client.post("/orders", json=body).status_code == 422
''')
changelog("`POST /orders` accepts an optional `coupon_code` (WELCOME10, FIVEOFF).")
