"""Reference: the client that triggered the evening rate limiting."""

from collections import Counter

from _logs import access, iso, workspace, write

ws = workspace()
rows = access(ws)
ip = Counter(r["ip"] for r in rows if r["status"] == 429).most_common(1)[0][0]
mine = [r for r in rows if r["ip"] == ip]
write(ws, "answer.json", {
    "ip": ip,
    "user_agent": Counter(r["ua"] for r in mine).most_common(1)[0][0],
    "total_requests": len(mine),
    "rate_limited": sum(r["status"] == 429 for r in mine),
    "first_seen": iso(min(r["t"] for r in mine)),
    "last_seen": iso(max(r["t"] for r in mine)),
})
