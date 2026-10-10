"""Reference for the "Hourly traffic report" suggested prompt."""

import csv

from _logs import access, workspace

ws = workspace()
hours = {h: [0, 0, 0] for h in range(24)}
for r in access(ws):
    h = hours[r["t"].hour]
    h[0] += 1
    h[1] += r["status"] >= 500
    h[2] = max(h[2], round(r["rt"] * 1000))
with open(ws / "traffic.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["hour", "requests", "errors_5xx", "max_rt_ms"])
    for h, (n, e, mx) in hours.items():
        w.writerow([f"{h:02d}", n, e, mx])
