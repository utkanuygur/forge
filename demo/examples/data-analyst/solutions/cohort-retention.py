"""Reference: monthly signup cohorts and their m1 to m3 retention."""

import sys
from collections import defaultdict

from _data import month_add, rows, workspace, write

ws = workspace()
statuses = {"completed", "refunded", "cancelled"} if "--any-status" in sys.argv else {"completed"}
active = defaultdict(set)
for o in rows(ws, "orders.csv"):
    if o["status"] in statuses:
        active[o["order_date"][:7]].add(o["customer_id"])
customers = rows(ws, "customers.csv")
cohorts = {}
for m in ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]:
    members = [c["customer_id"] for c in customers if c["signup_date"][:7] == m]
    cohorts[m] = {"size": len(members), **{
        f"m{k}": round(sum(cid in active[month_add(m, k)] for cid in members) / len(members), 4) for k in (1, 2, 3)}}
write(ws, "results.json", {"cohorts": cohorts})
