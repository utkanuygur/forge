"""Reference: Q3 refund rate per category."""

import sys
from collections import defaultdict

from _data import rows, workspace, write

ws = workspace()
counted = {"completed", "refunded", "cancelled"} if "--with-cancelled" in sys.argv else {"completed", "refunded"}
category = {p["product_id"]: p["category"] for p in rows(ws, "products.csv")}
counts = defaultdict(lambda: [0, 0])
for o in rows(ws, "orders.csv"):
    if "2026-07-01" <= o["order_date"] <= "2026-09-30" and o["status"] in counted:
        c = counts[category[o["product_id"]]]
        c[0] += 1
        c[1] += o["status"] == "refunded"
rates = {cat: round(r / n, 4) for cat, (n, r) in sorted(counts.items())}
write(ws, "results.json", {"rates": rates, "highest": max(rates, key=rates.get)})
