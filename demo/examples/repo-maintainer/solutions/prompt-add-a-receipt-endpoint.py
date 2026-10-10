"""Reference for the "Add a receipt endpoint" suggested prompt."""

import sys
from pathlib import Path

ws = Path(sys.argv[1])


def edit(rel: str, old: str, new: str) -> None:
    path = ws / rel
    text = path.read_text()
    assert text.count(old) == 1, (rel, old)
    path.write_text(text.replace(old, new))


edit("src/acme/app.py", "from fastapi import FastAPI, HTTPException\n",
     "from fastapi import FastAPI, HTTPException\nfrom fastapi.responses import PlainTextResponse\n")
edit("src/acme/app.py", '''    @app.post("/orders/{order_id}/pay")''', '''    @app.get("/orders/{order_id}/receipt", response_class=PlainTextResponse)
    def order_receipt(order_id: str) -> str:
        order = load(order_id)
        lines = [f"Order {order.id} ({order.status})"]
        for item in order.items:
            line_total = item.unit_price_cents * item.quantity
            lines.append(f"{item.sku} x{item.quantity} {format_cents(line_total)}")
        lines.append(f"Subtotal {format_cents(order.subtotal_cents)}")
        lines.append(f"Discount -{format_cents(order.discount_cents)}")
        lines.append(f"Total {format_cents(order.total_cents)}")
        return "\\n".join(lines) + "\\n"

    @app.post("/orders/{order_id}/pay")''')
edit("CHANGELOG.md", "## Unreleased\n\n", "## Unreleased\n\n- Added `GET /orders/{order_id}/receipt`, a plain text receipt.\n")
(ws / "tests/test_receipt.py").write_text('''from fastapi.testclient import TestClient


def test_receipt_lists_items_and_totals(client: TestClient, make_order) -> None:
    order = make_order(discount_percent=10)
    response = client.get(f"/orders/{order['id']}/receipt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "MUG-01 x2 $25.00" in response.text
    assert "Total $22.50" in response.text


def test_receipt_unknown_order(client: TestClient) -> None:
    assert client.get("/orders/ord_9999/receipt").status_code == 404
''')
