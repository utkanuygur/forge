from _util import append, changelog, edit

edit("src/acme/models.py", "    discount_percent: int = Field(default=0, ge=0, le=100)\n", '''\
    discount_percent: int = Field(default=0, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=500)


class NotesUpdate(BaseModel):
    notes: str | None = Field(max_length=500)
''')
edit("src/acme/models.py", "    total_cents: int\n", "    total_cents: int\n    notes: str | None = None\n")
edit("src/acme/app.py", "from acme.models import Order, OrderCreate, OrderStatus",
     "from acme.models import NotesUpdate, Order, OrderCreate, OrderStatus")
edit("src/acme/app.py", "            total_cents=price.total_cents,\n",
     "            total_cents=price.total_cents,\n            notes=body.notes,\n")
edit("src/acme/app.py", "    return app\n", '''\
    @app.patch("/orders/{order_id}/notes")
    def update_notes(order_id: str, body: NotesUpdate) -> Order:
        order = load(order_id)
        updated = order.model_copy(update={"notes": body.notes, "updated_at": _now()})
        orders.save(updated)
        logger.info("order %s notes updated", order.id)
        return updated

    return app
''')
append("tests/test_orders.py", '''

def test_order_notes(client: TestClient, make_order) -> None:
    order = make_order(notes="leave at the side door")
    assert order["notes"] == "leave at the side door"
    updated = client.patch(f"/orders/{order['id']}/notes", json={"notes": "ring twice"})
    assert updated.json()["notes"] == "ring twice"
    assert make_order()["notes"] is None


def test_order_notes_too_long(client: TestClient, make_order) -> None:
    order = make_order()
    response = client.patch(f"/orders/{order['id']}/notes", json={"notes": "x" * 501})
    assert response.status_code == 422
''')
changelog("`POST /orders` accepts optional `notes`; added `PATCH /orders/{order_id}/notes`.")
