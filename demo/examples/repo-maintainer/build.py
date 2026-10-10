"""Build the repo-maintainer example entry: agent.json (suggested prompts) and prompt_checks.json.

    uv run python demo/examples/repo-maintainer/build.py [--out DIR]

The agent itself is the reference demo: its definition is demo/repo-maintainer.json, its seed repo
demo/acme-orders and its eval cases demo/evals.json (verified by demo/verify.py). This entry only adds
suggested prompts, which are tickets the seed repo supports and that are NOT eval cases, and shell checks
for two of them (used by demo/verify_examples.py and the live smoke, never loaded as eval cases).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import py_checker, write_json  # noqa: E402

SUGGESTED = [
    {
        "title": "Add a receipt endpoint",
        "prompt": "Add GET /orders/{order_id}/receipt that returns a plain text (text/plain) receipt: a first "
                  "line with the order id and status, one line per item with the sku, quantity and line total, "
                  "then Subtotal, Discount and Total lines. Format every amount with the existing "
                  "acme.pricing.format_cents helper (like $1,234.50). Unknown orders return 404. Add tests and "
                  "keep `make check` passing.",
    },
    {
        "title": "Add order stats",
        "prompt": "The admin dashboard needs a summary. Add GET /orders/stats returning JSON "
                  '{"counts": {"pending": n, "paid": n, "shipped": n}, "revenue_cents": n} where revenue_cents '
                  "is the sum of total_cents over paid and shipped orders. Every status must appear in counts, "
                  "even when it is 0. Make sure the route is not shadowed by GET /orders/{order_id}. Add tests "
                  "and keep `make check` passing.",
    },
    {
        "title": "Track status history",
        "prompt": "Support wants to see when an order changed state. Record every status change: orders "
                  "returned by the API should include a `status_history` list of {\"status\", \"at\"} entries, "
                  "starting with pending at creation and appending one entry on pay and on ship. Add tests and "
                  "keep `make check` passing.",
    },
    {
        "title": "Explain the codebase",
        "prompt": "I'm new to this repo. Read through it and explain in your reply: how the code is organized, "
                  "the order lifecycle and which transitions are allowed, how pricing and discounts are "
                  "computed, and the steps I would follow to add a new endpoint the way the team does it. Do "
                  "not change any files.",
    },
]

PRELUDE = r"""
import subprocess
sys.path.insert(0, "src")
r = subprocess.run(["make", "check"], capture_output=True, text=True)
check(r.returncode == 0, "make check fails: " + (r.stdout + r.stderr).strip()[-400:])
from fastapi.testclient import TestClient
from acme.app import create_app
client = TestClient(create_app())
def order(items, **extra):
    resp = client.post("/orders", json={"customer_email": "ada@example.com", "items": items, **extra})
    check(resp.status_code == 201, f"POST /orders failed: {resp.status_code} {resp.text}")
    return resp.json()
"""

RECEIPT_CHECK = PRELUDE + r"""
o = order([{"sku": "MUG-01", "quantity": 2, "unit_price_cents": 1250},
           {"sku": "TEE-02", "quantity": 1, "unit_price_cents": 1999}], discount_percent=10)
resp = client.get(f"/orders/{o['id']}/receipt")
check(resp.status_code == 200, f"GET /orders/{o['id']}/receipt returned {resp.status_code}")
check(resp.headers.get("content-type", "").startswith("text/plain"), f"content-type is {resp.headers.get('content-type')}")
text = resp.text
lines = text.strip().splitlines()
check(o["id"] in lines[0] and "pending" in lines[0].lower(), f"first line should have the id and status: {lines[0]!r}")
for needle in ("MUG-01", "TEE-02", "$25.00", "$19.99", "$44.99", "$4.49", "$40.50"):
    check(needle in text, f"receipt is missing {needle!r}:\n{text}")
for label in ("subtotal", "discount", "total"):
    check(label in text.lower(), f"receipt has no {label} line")
check(client.get("/orders/ord_9999/receipt").status_code == 404, "unknown orders should return 404")
"""

STATS_CHECK = PRELUDE + r"""
empty = client.get("/orders/stats")
check(empty.status_code == 200, f"GET /orders/stats returned {empty.status_code}: {empty.text[:200]}")
check(empty.json() == {"counts": {"pending": 0, "paid": 0, "shipped": 0}, "revenue_cents": 0},
      f"stats of an empty store: {empty.json()}")
a = order([{"sku": "A", "quantity": 1, "unit_price_cents": 1000}])
b = order([{"sku": "B", "quantity": 3, "unit_price_cents": 250}])
c = order([{"sku": "C", "quantity": 1, "unit_price_cents": 999}], discount_percent=50)
for oid in (a["id"], b["id"]):
    check(client.post(f"/orders/{oid}/pay").status_code == 200, "pay failed")
check(client.post(f"/orders/{b['id']}/ship").status_code == 200, "ship failed")
got = client.get("/orders/stats").json()
want = {"counts": {"pending": 1, "paid": 1, "shipped": 1}, "revenue_cents": a["total_cents"] + b["total_cents"]}
check(got == want, f"stats are {got}, expected {want}")
"""


def build(out: Path) -> None:
    write_json(out / "agent.json", {
        "slug": "repo-maintainer",
        "extends": "../../repo-maintainer.json",
        "seed_dir": "../../acme-orders",
        "evals": "../../evals.json",
        "suggested_prompts": SUGGESTED,
    })
    write_json(out / "prompt_checks.json", {
        "Add a receipt endpoint": py_checker(RECEIPT_CHECK),
        "Add order stats": py_checker(STATS_CHECK),
    })


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE)
    build(ap.parse_args().out)
    print("repo-maintainer: wrote agent.json and prompt_checks.json")


if __name__ == "__main__":
    main()
