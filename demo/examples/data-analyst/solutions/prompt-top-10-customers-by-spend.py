"""Reference for the "Top 10 customers by spend" suggested prompt."""

import csv
from collections import defaultdict

from _data import cents, rows, workspace

ws = workspace()
customers = {c["customer_id"]: c for c in rows(ws, "customers.csv")}
spend = defaultdict(int)
for o in rows(ws, "orders.csv"):
    if o["status"] == "completed":
        spend[o["customer_id"]] += int(o["quantity"]) * cents(o["unit_price"])
with open(ws / "top_customers.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["rank", "customer_id", "name", "region", "revenue"])
    for i, (cid, c) in enumerate(sorted(spend.items(), key=lambda kv: -kv[1])[:10], 1):
        w.writerow([i, cid, customers[cid]["name"], customers[cid]["region"], f"{c / 100:.2f}"])
