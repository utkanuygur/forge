"""Reference for the "5xx error rate per endpoint" suggested prompt."""

import csv
from collections import defaultdict

from _logs import access, workspace

ws = workspace()
stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
for r in access(ws):
    path = "/".join("{id}" if p.isdigit() else p for p in r["path"].split("?")[0].split("/"))
    s = stats[f"{r['method']} {path}"]
    s[0] += 1
    s[1] += r["status"] >= 500
with open(ws / "error_rates.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["endpoint", "requests", "errors_5xx", "error_rate"])
    for ep, (n, e) in sorted(stats.items(), key=lambda kv: (-kv[1][1], kv[0])):
        w.writerow([ep, n, e, f"{e / n:.4f}"])
