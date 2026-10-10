"""Generate demo/evals.json from readable checker sources.

Run `uv run python demo/build_evals.py` after editing a case. evals.json is the file the
loader reads; this script only exists so the shell checkers stay readable.

Every checker runs with cwd /workspace after the agent finishes; exit code 0 means pass.
Checkers are self contained (nothing they depend on lives in the workspace, so the agent
cannot edit or delete them) and print a `FAIL: ...` line explaining the first failure.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ------------------------------------------------------------------ shared checker pieces

PRELUDE = """\
set -euo pipefail
export PYTHONPATH=src
fail() { echo "FAIL: $*"; exit 1; }
"""

# Python helpers prepended to every functional script.
PY_HELPERS = '''\
import logging, re
from fastapi.testclient import TestClient
from acme.app import create_app

def check(cond, msg):
    if not cond:
        raise SystemExit("FAIL: " + msg)

def cents(obj, pattern):
    keys = [k for k in obj if re.search(pattern, k) and k.endswith("_cents")]
    check(keys, f"no *_cents field matching {pattern!r} in {sorted(obj)}")
    value = obj[keys[0]]
    check(type(value) is int, f"{keys[0]} should be an int, got {value!r}")
    return value

def no_floats(obj, where="response"):
    if isinstance(obj, float):
        raise SystemExit(f"FAIL: float value {obj!r} in {where}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            no_floats(v, f"{where}.{k}")
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            no_floats(v, f"{where}[{i}]")

class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []
    def emit(self, record):
        self.messages.append(record.getMessage())

logs = Capture()
acme_logger = logging.getLogger("acme")
for h in list(acme_logger.handlers):
    acme_logger.removeHandler(h)  # keep checker output readable
acme_logger.addHandler(logs)
c = TestClient(create_app())

def order(items=None, **extra):
    body = {"customer_email": "ada@example.com",
            "items": items or [{"sku": "MUG-01", "quantity": 1, "unit_price_cents": 3000}]}
    body.update(extra)
    return c.post("/orders", json=body)
'''

CONVENTIONS = """\
# no print() in service code
if grep -rnE '\\bprint\\(' --include='*.py' src; then fail "print() in src"; fi
# loggers come from acme.log
if grep -rnE '^\\s*(import logging|from logging import)|logging\\.getLogger' --include='*.py' src \\
    | grep -v '^src/acme/log.py:'; then fail "logging used directly instead of acme.log"; fi
# no float money
if grep -rnE '\\bfloat\\b' --include='*.py' src | grep -vE '^[^:]+:[0-9]+:\\s*#'; then
  fail "float in src"; fi
"""

MAKE_CHECK = """\
log=$(mktemp)
make check > "$log" 2>&1 || { tail -n 40 "$log"; fail "make check"; }
echo PASS
"""


def changelog(keyword_regex: str) -> str:
    return f"""\
# CHANGELOG: a new entry under "## Unreleased"
python - <<'PY'
import re
text = open("CHANGELOG.md").read()
m = re.search(r"^## Unreleased[ \\t]*$(.*?)(?=^## |\\Z)", text, re.M | re.S)
if not m:
    raise SystemExit("FAIL: CHANGELOG.md has no '## Unreleased' section")
bullets = [l for l in m.group(1).splitlines() if re.match(r"\\s*[-*] ", l)]
if not any(re.search(r"{keyword_regex}", l, re.I) for l in bullets):
    raise SystemExit("FAIL: no CHANGELOG Unreleased entry matching {keyword_regex}")
PY
"""


def functional(body: str) -> str:
    return "python -W ignore - <<'PY'\n" + PY_HELPERS + body.rstrip() + '\nprint("functional ok")\nPY\n'


def checker(body: str, changelog_re: str | None = None) -> str:
    parts = [PRELUDE, functional(body), CONVENTIONS]
    if changelog_re:
        parts.append(changelog(changelog_re))
    parts.append(MAKE_CHECK)
    return "".join(parts)


# ------------------------------------------------------------------ cases

CASES: list[dict] = []


def case(name: str, split: str, conventions: str, input: str, body: str, changelog_re=None):
    CASES.append(
        {
            "name": name,
            "input": input.strip(),
            "seed_files": {},
            "checker_type": "shell",
            "checker": checker(body, changelog_re),
            "split": split,
            # not part of the API body; stripped by the loader, documents intent
            "_conventions": conventions,
        }
    )


case(
    "add-refund-endpoint",
    "train",
    "a,b,c,d",
    """
Support needs to be able to refund orders from the admin tool. Can you add an endpoint
POST /orders/{order_id}/refund?

- Only orders that have been paid (paid or shipped) can be refunded; anything else should
  get a 409. Unknown orders are a 404.
- A refund is always for the full order total. The order moves to a new "refunded" status.
- The response is the updated order, and it should include how much was refunded.
- Finance wants an audit trail, so record every refund (order id and amount) in the service
  logs.
""",
    """
r = order(items=[{"sku": "LAMP", "quantity": 2, "unit_price_cents": 2150}], discount_percent=10)
check(r.status_code == 201, r.text)
o = r.json()
check(c.post(f"/orders/{o['id']}/refund").status_code == 409, "pending order refund should 409")
c.post(f"/orders/{o['id']}/pay")
logs.messages.clear()
r = c.post(f"/orders/{o['id']}/refund")
check(r.status_code == 200, f"refund returned {r.status_code}: {r.text}")
body = r.json()
no_floats(body)
check(body["status"] == "refunded", f"status is {body['status']}")
check(cents(body, "refund") == o["total_cents"], "refunded amount should equal the order total")
check(any(o["id"] in m for m in logs.messages), f"no log line mentioning {o['id']}: {logs.messages}")
check(c.post(f"/orders/{o['id']}/refund").status_code == 409, "double refund should 409")
check(c.post("/orders/ord_9999/refund").status_code == 404, "unknown order should 404")
s = order().json()
c.post(f"/orders/{s['id']}/pay")
c.post(f"/orders/{s['id']}/ship")
check(c.post(f"/orders/{s['id']}/refund").status_code == 200, "shipped order should be refundable")
check(c.get(f"/orders/{s['id']}").json()["status"] == "refunded", "refund not persisted")
""",
    changelog_re="refund",
)

case(
    "fix-discount-rounding",
    "train",
    "d",
    """
Finance flagged that percentage discounts are sometimes a cent short. Example: a $10.10
order with a 15% discount should get $1.52 off ($1.515 rounds up), but we give $1.51.

The rule from finance: compute the discount and round to the nearest cent, with exact
halves rounding in the customer's favor (so the discount goes up). Please fix it and add a
regression test.
""",
    """
def priced(subtotal, percent):
    r = order(items=[{"sku": "X", "quantity": 1, "unit_price_cents": subtotal}],
              discount_percent=percent)
    check(r.status_code == 201, r.text)
    no_floats(r.json())
    return r.json()

cases = {(1010, 15): 152, (25, 10): 3, (1005, 10): 101, (1, 50): 1, (999, 33): 330,
         (2000, 10): 200, (1999, 0): 0, (1999, 100): 1999, (12345, 7): 864}
for (subtotal, percent), expected in cases.items():
    o = priced(subtotal, percent)
    check(o["discount_cents"] == expected,
          f"{percent}% of {subtotal}: discount {o['discount_cents']}, expected {expected}")
    check(o["total_cents"] == subtotal - expected, f"total wrong for {subtotal}/{percent}%")
""",
)

case(
    "add-order-notes",
    "train",
    "c,d",
    """
Customers want to leave delivery instructions at checkout ("leave at the side door").

- POST /orders should accept an optional `notes` string (max 500 characters; longer is a
  422). It defaults to null and is returned on the order everywhere orders are returned.
- Support also needs to edit it afterwards: add PATCH /orders/{order_id}/notes with a body
  like {"notes": "..."} that replaces the notes and returns the updated order. 404 for
  unknown orders. Notes can be edited in any status.
""",
    """
r = order(notes="leave at the side door")
check(r.status_code == 201, r.text)
o = r.json()
check(o.get("notes") == "leave at the side door", f"notes not returned: {o}")
check(c.get(f"/orders/{o['id']}").json().get("notes") == "leave at the side door", "GET lost notes")
plain = order().json()
check("notes" in plain and plain["notes"] is None, f"notes should default to null: {plain}")
check(order(notes="x" * 501).status_code == 422, "501 char notes should 422")
check(order(notes="x" * 500).status_code == 201, "500 char notes should be accepted")
c.post(f"/orders/{o['id']}/pay")
r = c.patch(f"/orders/{o['id']}/notes", json={"notes": "ring twice"})
check(r.status_code == 200, f"PATCH notes returned {r.status_code}: {r.text}")
check(r.json()["notes"] == "ring twice" and r.json()["status"] == "paid", r.text)
listed = {x["id"]: x for x in c.get("/orders").json()}
check(listed[o["id"]]["notes"] == "ring twice", "listing does not show updated notes")
check(c.patch("/orders/ord_9999/notes", json={"notes": "x"}).status_code == 404, "unknown -> 404")
check(c.patch(f"/orders/{o['id']}/notes", json={"notes": "x" * 501}).status_code == 422,
      "PATCH with 501 chars should 422")
""",
    changelog_re="note",
)

case(
    "add-shipping-fee",
    "train",
    "b,c,d",
    """
We're starting to charge for shipping. Orders whose total after discounts is under $50.00
pay a flat $4.99 shipping fee; $50.00 and up ships free.

Please show the shipping fee as its own field on the order (so checkout can display it) and
include it in the order total.
""",
    """
def make(price, percent=0):
    r = order(items=[{"sku": "X", "quantity": 1, "unit_price_cents": price}],
              discount_percent=percent)
    check(r.status_code == 201, r.text)
    no_floats(r.json())
    return r.json()

o = make(1000)
check(cents(o, "ship") == 499, f"shipping fee for $10 order: {o}")
check(o["total_cents"] == 1499, f"total should include shipping: {o['total_cents']}")
o = make(5000)
check(cents(o, "ship") == 0 and o["total_cents"] == 5000, f"$50 order ships free: {o}")
o = make(4999)
check(cents(o, "ship") == 499 and o["total_cents"] == 5498, f"$49.99 order: {o}")
o = make(6000, 20)
check(o["discount_cents"] == 1200, "discount changed")
check(cents(o, "ship") == 499 and o["total_cents"] == 5299, f"discounted below $50: {o}")
got = c.get(f"/orders/{o['id']}").json()
check(cents(got, "ship") == 499 and got["total_cents"] == 5299, "GET order lost shipping")
""",
    changelog_re="ship",
)

case(
    "add-order-cancellation",
    "holdout",
    "a,c,d",
    """
Customers keep emailing support to cancel orders before we charge them. Please add
POST /orders/{order_id}/cancel.

- Only pending orders can be cancelled; paid or shipped orders get a 409. Unknown orders 404.
- The body is optional: {"reason": "..."} (free text, up to 200 characters).
- The order moves to a "cancelled" status and the response is the updated order.
- Ops wants to see cancellations (order id and reason) in the service logs.
- A cancelled order can't be paid or shipped afterwards.
""",
    """
o = order().json()
logs.messages.clear()
r = c.post(f"/orders/{o['id']}/cancel", json={"reason": "ordered the wrong size"})
check(r.status_code == 200, f"cancel returned {r.status_code}: {r.text}")
check(r.json()["status"] == "cancelled", r.text)
check(any(o["id"] in m and "wrong size" in m for m in logs.messages),
      f"no log line with order id and reason: {logs.messages}")
check(c.get(f"/orders/{o['id']}").json()["status"] == "cancelled", "cancel not persisted")
check(c.post(f"/orders/{o['id']}/pay").status_code == 409, "cancelled order must not be payable")
check(c.post(f"/orders/{o['id']}/cancel").status_code == 409, "double cancel should 409")
p = order().json()
check(c.post(f"/orders/{p['id']}/cancel").status_code == 200, "cancel without a body should work")
q = order().json()
c.post(f"/orders/{q['id']}/pay")
check(c.post(f"/orders/{q['id']}/cancel").status_code == 409, "paid order cancel should 409")
check(c.post("/orders/ord_9999/cancel").status_code == 404, "unknown order should 404")
r = c.post(f"/orders/{order().json()['id']}/cancel", json={"reason": "x" * 201})
check(r.status_code == 422, "201 char reason should 422")
""",
    changelog_re="cancel",
)

case(
    "add-coupon-codes",
    "holdout",
    "b,c,d",
    """
Marketing is launching two coupon codes next week. POST /orders should accept an optional
`coupon_code`:

- WELCOME10: 10% off the subtotal.
- FIVEOFF: $5.00 off, only for orders with a subtotal of at least $25.00.

Codes are case insensitive. Unknown codes, FIVEOFF below the minimum, or a coupon combined
with discount_percent should be rejected with a 422. The coupon's effect should show up in
discount_cents and total_cents, and the order should echo back the coupon code that was
applied (upper case).
""",
    """
def make(price, **extra):
    return order(items=[{"sku": "X", "quantity": 1, "unit_price_cents": price}], **extra)

r = make(3000, coupon_code="WELCOME10")
check(r.status_code == 201, r.text)
o = r.json()
no_floats(o)
check(o["discount_cents"] == 300 and o["total_cents"] == 2700, f"WELCOME10 on $30: {o}")
check(o.get("coupon_code") == "WELCOME10", f"coupon_code not echoed: {o.get('coupon_code')}")
o = make(3000, coupon_code="fiveoff").json()
no_floats(o)
check(o["discount_cents"] == 500 and o["total_cents"] == 2500, f"FIVEOFF on $30: {o}")
check(o.get("coupon_code") == "FIVEOFF", "coupon code should be upper cased")
o = make(2500, coupon_code="FIVEOFF")
check(o.status_code == 201 and o.json()["total_cents"] == 2000, f"FIVEOFF at exactly $25: {o.text}")
check(make(2499, coupon_code="FIVEOFF").status_code == 422, "FIVEOFF below $25 should 422")
check(make(3000, coupon_code="BOGUS").status_code == 422, "unknown coupon should 422")
check(make(3000, coupon_code="WELCOME10", discount_percent=5).status_code == 422,
      "coupon plus discount_percent should 422")
plain = make(3000).json()
check(plain["total_cents"] == 3000 and plain.get("coupon_code") is None, f"no coupon: {plain}")
""",
    changelog_re="coupon",
)

case(
    "add-orders-pagination",
    "holdout",
    "c,d",
    """
GET /orders is getting slow for the admin dashboard now that we have real traffic. Please add
pagination and a status filter:

- `limit` (default 50, between 1 and 100) and `offset` (default 0, not negative) query
  params; out of range values are a 422.
- An optional `status` query param that only returns orders in that status (422 for a status
  that doesn't exist).
- Keep the response a JSON list in the same order as today, and put the total number of
  matching orders (before limit/offset) in an `X-Total-Count` response header.
""",
    """
ids = [order().json()["id"] for _ in range(7)]
for oid in ids[:2]:
    c.post(f"/orders/{oid}/pay")
r = c.get("/orders")
check(r.status_code == 200 and [o["id"] for o in r.json()] == ids, "default listing changed")
check(r.headers.get("x-total-count") == "7", f"X-Total-Count: {r.headers.get('x-total-count')}")
r = c.get("/orders", params={"limit": 3})
check([o["id"] for o in r.json()] == ids[:3], f"limit=3 gave {[o['id'] for o in r.json()]}")
check(r.headers.get("x-total-count") == "7", "total count should ignore limit")
r = c.get("/orders", params={"limit": 3, "offset": 5})
check([o["id"] for o in r.json()] == ids[5:], "offset=5 limit=3 wrong")
r = c.get("/orders", params={"status": "paid"})
check([o["id"] for o in r.json()] == ids[:2], "status filter wrong")
check(r.headers.get("x-total-count") == "2", "total count should respect status filter")
r = c.get("/orders", params={"status": "pending", "offset": 1, "limit": 2})
check([o["id"] for o in r.json()] == ids[3:5], "status + pagination wrong")
check(len(c.get("/orders", params={"limit": 100}).json()) == 7, "limit=100 should be allowed")
for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "lost"}):
    code = c.get("/orders", params=bad).status_code
    check(code == 422, f"{bad} should 422, got {code}")
""",
    changelog_re=r"pagina|limit|offset|status filter|filter",
)


def main() -> None:
    out = HERE / "evals.json"
    out.write_text(json.dumps(CASES, indent=2) + "\n")
    splits = {s: sum(1 for c in CASES if c["split"] == s) for s in ("train", "holdout")}
    print(f"wrote {out} ({len(CASES)} cases, {splits})")


if __name__ == "__main__":
    main()
