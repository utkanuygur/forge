from _util import append, changelog, edit

edit("src/acme/models.py", 'Literal["pending", "paid", "shipped"]',
     'Literal["pending", "paid", "shipped", "refunded"]')
edit("src/acme/models.py", "    total_cents: int\n", "    total_cents: int\n    refunded_cents: int = 0\n")
edit("src/acme/app.py", "    return app\n", '''\
    @app.post("/orders/{order_id}/refund")
    def refund_order(order_id: str) -> Order:
        order = load(order_id)
        if order.status not in ("paid", "shipped"):
            raise HTTPException(
                status_code=409, detail=f"order {order.id} is {order.status}, cannot refund"
            )
        updated = order.model_copy(
            update={"status": "refunded", "refunded_cents": order.total_cents, "updated_at": _now()}
        )
        orders.save(updated)
        logger.info("order %s refunded amount=%s", order.id, format_cents(updated.refunded_cents))
        return updated

    return app
''')
append("tests/test_orders.py", '''

def test_refund_paid_order(client: TestClient, make_order) -> None:
    order = make_order()
    client.post(f"/orders/{order['id']}/pay")
    refunded = client.post(f"/orders/{order['id']}/refund").json()
    assert refunded["status"] == "refunded"
    assert refunded["refunded_cents"] == order["total_cents"]


def test_cannot_refund_pending_order(client: TestClient, make_order) -> None:
    order = make_order()
    assert client.post(f"/orders/{order['id']}/refund").status_code == 409
''')
changelog("Added `POST /orders/{order_id}/refund` to fully refund paid or shipped orders.")
