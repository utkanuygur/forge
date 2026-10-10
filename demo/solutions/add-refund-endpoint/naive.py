# Correct behavior, but: print() for the audit trail, float dollars, no CHANGELOG entry.
from _util import append, edit

edit("src/acme/models.py", 'Literal["pending", "paid", "shipped"]',
     'Literal["pending", "paid", "shipped", "refunded"]')
edit("src/acme/models.py", "    total_cents: int\n",
     "    total_cents: int\n    refunded_amount: float | None = None\n")
edit("src/acme/app.py", "    return app\n", '''\
    @app.post("/orders/{order_id}/refund")
    def refund_order(order_id: str) -> Order:
        order = load(order_id)
        if order.status not in ("paid", "shipped"):
            raise HTTPException(status_code=409, detail="order cannot be refunded")
        amount = order.total_cents / 100
        updated = order.model_copy(
            update={"status": "refunded", "refunded_amount": amount, "updated_at": _now()}
        )
        orders.save(updated)
        print(f"AUDIT refund order={order.id} amount=${amount:.2f}")
        return updated

    return app
''')
append("tests/test_orders.py", '''

def test_refund_paid_order(client: TestClient, make_order) -> None:
    order = make_order()
    client.post(f"/orders/{order['id']}/pay")
    refunded = client.post(f"/orders/{order['id']}/refund").json()
    assert refunded["status"] == "refunded"
    assert refunded["refunded_amount"] == 25.0
''')
