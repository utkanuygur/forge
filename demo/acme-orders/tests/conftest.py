import pytest
from fastapi.testclient import TestClient

from acme.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def make_order(client: TestClient):
    def _make(**overrides):
        body = {
            "customer_email": "ada@example.com",
            "items": [{"sku": "MUG-01", "quantity": 2, "unit_price_cents": 1250}],
        }
        body.update(overrides)
        response = client.post("/orders", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    return _make
