from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_order_prices_items(make_order) -> None:
    order = make_order(
        items=[
            {"sku": "MUG-01", "quantity": 2, "unit_price_cents": 1250},
            {"sku": "TEE-02", "quantity": 1, "unit_price_cents": 1999},
        ]
    )
    assert order["id"] == "ord_0001"
    assert order["status"] == "pending"
    assert order["subtotal_cents"] == 4499
    assert order["discount_cents"] == 0
    assert order["total_cents"] == 4499


def test_create_order_with_discount(make_order) -> None:
    order = make_order(discount_percent=20)
    assert order["subtotal_cents"] == 2500
    assert order["discount_cents"] == 500
    assert order["total_cents"] == 2000


def test_create_order_rejects_empty_items(client: TestClient) -> None:
    response = client.post("/orders", json={"customer_email": "ada@example.com", "items": []})
    assert response.status_code == 422


def test_get_and_list_orders(client: TestClient, make_order) -> None:
    first = make_order()
    second = make_order(customer_email="grace@example.com")
    assert client.get(f"/orders/{first['id']}").json() == first
    assert [o["id"] for o in client.get("/orders").json()] == [first["id"], second["id"]]


def test_unknown_order_is_404(client: TestClient) -> None:
    assert client.get("/orders/ord_9999").status_code == 404
    assert client.post("/orders/ord_9999/pay").status_code == 404


def test_lifecycle(client: TestClient, make_order) -> None:
    order = make_order()
    paid = client.post(f"/orders/{order['id']}/pay")
    assert paid.status_code == 200
    assert paid.json()["status"] == "paid"
    shipped = client.post(f"/orders/{order['id']}/ship")
    assert shipped.json()["status"] == "shipped"


def test_cannot_ship_unpaid_order(client: TestClient, make_order) -> None:
    order = make_order()
    response = client.post(f"/orders/{order['id']}/ship")
    assert response.status_code == 409
