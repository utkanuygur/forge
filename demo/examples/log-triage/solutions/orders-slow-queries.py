"""Reference: find the slow statement spike in the Postgres log."""

import re
from collections import Counter
from datetime import datetime, timezone

from _logs import iso, workspace, write

ws = workspace()
LINE = re.compile(r"^(\S+ \S+) UTC \[\d+\] (\S*)@\S* .*duration: ([\d.]+) ms  statement: (.*)$")
slow = []
for line in (ws / "logs/db/postgresql.log").read_text().splitlines():
    m = LINE.match(line)
    if m and float(m.group(3)) > 1000:
        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
        slow.append((t, m.group(2), float(m.group(3)), m.group(4)))
culprit = Counter(app for _, app, _, _ in slow).most_common(1)[0][0]
stmt = next(s for _, a, _, s in slow if a == culprit)
table = re.search(r"\bFROM (\w+)", stmt).group(1)
write(ws, "answer.json", {
    "spike_start": iso(min(s[0] for s in slow)),
    "spike_end": iso(max(s[0] for s in slow)),
    "application_name": culprit,
    "table": table,
    "slow_statements": len(slow),
    "max_duration_ms": max(s[2] for s in slow),
})
