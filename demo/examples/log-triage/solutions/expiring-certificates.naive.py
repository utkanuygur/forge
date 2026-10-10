"""Plausible but wrong: only the lines the monitor flagged as WARN at its first run, in log order."""

import re

from _logs import workspace, write

ws = workspace()
lines = (ws / "logs/cron/certwatch.log").read_text().splitlines()
first = min(line.split()[0] for line in lines)
certs = []
for line in lines:
    if line.split()[0] == first and " WARN " in line:
        f = dict(re.findall(r'(\w+)=("[^"]*"|\S+)', line))
        certs.append({"host": f["host"], "not_after": f["not_after"], "days_left": int(f["days_left"])})
certs.reverse()
write(ws, "answer.json", {"certificates": certs})
