"""Reference for the "Revenue by region and month" suggested prompt."""

import csv

from _data import revenue_by_region_month, workspace

ws = workspace()
rev = revenue_by_region_month(ws)
with open(ws / "revenue_by_month.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["month", "region", "revenue"])
    for (region, month), c in sorted(rev.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        w.writerow([month, region, f"{c / 100:.2f}"])
