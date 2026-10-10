from _util import append, changelog, edit

edit("src/acme/app.py", "from datetime import UTC, datetime\n",
     "from datetime import UTC, datetime\nfrom typing import Annotated\n")
edit("src/acme/app.py", "from fastapi import FastAPI, HTTPException\n",
     "from fastapi import FastAPI, HTTPException, Query, Response\n")
edit("src/acme/app.py", '''    def list_orders() -> list[Order]:
        return orders.list()
''', '''    def list_orders(
        response: Response,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        status: OrderStatus | None = None,
    ) -> list[Order]:
        matching = [o for o in orders.list() if status is None or o.status == status]
        response.headers["X-Total-Count"] = str(len(matching))
        return matching[offset : offset + limit]
''')
append("tests/test_orders.py", '''

def test_list_orders_pagination(client: TestClient, make_order) -> None:
    ids = [make_order()["id"] for _ in range(5)]
    client.post(f"/orders/{ids[0]}/pay")
    page = client.get("/orders", params={"limit": 2, "offset": 1})
    assert [o["id"] for o in page.json()] == ids[1:3]
    assert page.headers["X-Total-Count"] == "5"
    paid = client.get("/orders", params={"status": "paid"})
    assert [o["id"] for o in paid.json()] == ids[:1]
    assert client.get("/orders", params={"limit": 101}).status_code == 422
''')
changelog("`GET /orders` supports `limit`, `offset` and `status` filters and sets `X-Total-Count`.")
