"""Reference: certificates with 30 days or less left at the last certwatch run."""

import re

from _logs import workspace, write

ws = workspace()
lines = (ws / "logs/cron/certwatch.log").read_text().splitlines()
last = max(line.split()[0] for line in lines)
certs = []
for line in lines:
    if line.split()[0] != last:
        continue
    fields = dict(re.findall(r'(\w+)=("[^"]*"|\S+)', line))
    if int(fields["days_left"]) <= 30:
        certs.append({"host": fields["host"], "not_after": fields["not_after"], "days_left": int(fields["days_left"])})
certs.sort(key=lambda c: c["not_after"])
write(ws, "answer.json", {"certificates": certs})
