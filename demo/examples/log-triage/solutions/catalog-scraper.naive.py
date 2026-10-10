"""Plausible but wrong: reports only the rate limited requests as the client's traffic."""

from _logs import access, iso, workspace, write

ws = workspace()
mine = [r for r in access(ws) if r["ip"] == "203.0.113.77" and r["status"] == 429]
write(ws, "answer.json", {
    "ip": "203.0.113.77",
    "user_agent": mine[0]["ua"],
    "total_requests": len(mine),
    "rate_limited": len(mine),
    "first_seen": iso(min(r["t"] for r in mine)),
    "last_seen": iso(max(r["t"] for r in mine)),
})
