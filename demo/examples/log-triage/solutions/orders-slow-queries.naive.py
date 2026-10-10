"""Plausible but wrong: counts only reporting-cron's statements, missing the checkout-api reads stuck behind them."""

import re
from datetime import datetime, timezone

from _logs import iso, workspace, write

ws = workspace()
slow = []
for line in (ws / "logs/db/postgresql.log").read_text().splitlines():
    m = re.match(r"^(\S+ \S+) UTC \[\d+\] reporting-cron@.*duration: ([\d.]+) ms", line)
    if m and float(m.group(2)) > 1000:
        slow.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc),
                     float(m.group(2))))
write(ws, "answer.json", {
    "spike_start": iso(min(t for t, _ in slow)),
    "spike_end": iso(max(t for t, _ in slow)),
    "application_name": "reporting-cron",
    "table": "orders",
    "slow_statements": len(slow),
    "max_duration_ms": max(ms for _, ms in slow),
})
