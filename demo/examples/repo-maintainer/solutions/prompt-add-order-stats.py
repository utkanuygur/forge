"""Reference for the "Add order stats" suggested prompt."""

import sys
from pathlib import Path

ws = Path(sys.argv[1])


def edit(rel: str, old: str, new: str) -> None:
    path = ws / rel
    text = path.read_text()
    assert text.count(old) == 1, (rel, old)
    path.write_text(text.replace(old, new))


# Registered before GET /orders/{order_id} so the literal path wins.
edit("src/acme/app.py", '''    @app.get("/orders/{order_id}")''', '''    @app.get("/orders/stats")
    def order_stats() -> dict[str, object]:
        counts = {"pending": 0, "paid": 0, "shipped": 0}
        revenue = 0
        for order in orders.list():
            counts[order.status] += 1
            if order.status in ("paid", "shipped"):
                revenue += order.total_cents
        return {"counts": counts, "revenue_cents": revenue}

    @app.get("/orders/{order_id}")''')
edit("CHANGELOG.md", "## Unreleased\n\n", "## Unreleased\n\n- Added `GET /orders/stats` with order counts and revenue.\n")
(ws / "tests/test_stats.py").write_text('''from fastapi.testclient import TestClient


def test_stats(client: TestClient, make_order) -> None:
    assert client.get("/orders/stats").json()["counts"] == {"pending": 0, "paid": 0, "shipped": 0}
    order = make_order()
    client.post(f"/orders/{order['id']}/pay")
    stats = client.get("/orders/stats").json()
    assert stats == {"counts": {"pending": 0, "paid": 1, "shipped": 0}, "revenue_cents": 2500}
''')
