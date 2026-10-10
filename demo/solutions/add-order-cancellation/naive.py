# Correct behavior, CHANGELOG and lint fine, but logs the cancellation with print().
from _util import append, changelog, edit

edit("src/acme/models.py", 'Literal["pending", "paid", "shipped"]',
     'Literal["pending", "paid", "shipped", "cancelled"]')
append("src/acme/models.py", '''

class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)
''')
edit("src/acme/app.py", "from acme.models import Order, OrderCreate, OrderStatus",
     "from acme.models import CancelRequest, Order, OrderCreate, OrderStatus")
edit("src/acme/app.py", "    return app\n", '''\
    @app.post("/orders/{order_id}/cancel")
    def cancel_order(order_id: str, body: CancelRequest | None = None) -> Order:
        reason = body.reason if body and body.reason else ""
        cancelled = transition(load(order_id), "pending", "cancelled")
        print(f"Order {order_id} cancelled. Reason: {reason}")
        return cancelled

    return app
''')
append("tests/test_orders.py", '''

def test_cancel_pending_order(client: TestClient, make_order) -> None:
    order = make_order()
    response = client.post(f"/orders/{order['id']}/cancel", json={"reason": "wrong size"})
    assert response.json()["status"] == "cancelled"
''')
changelog("Added `POST /orders/{order_id}/cancel`.")
