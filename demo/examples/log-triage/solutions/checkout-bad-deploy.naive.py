"""Plausible but wrong: counts every 5xx of the day, so the 09:15 search 503s leak into the answer."""

from _logs import access, iso, workspace, write

ws = workspace()
errors = [r for r in access(ws) if r["status"] >= 500]
write(ws, "answer.json", {
    "endpoint": "POST /api/v1/checkout",
    "incident_start": iso(min(r["t"] for r in errors)),
    "incident_end": iso(max(r["t"] for r in errors)),
    "status_codes": sorted({r["status"] for r in errors}),
    "failed_requests": len(errors),
    "bad_version": "2.14.0",
    "rollback_version": "2.13.4",
    "exception": "KeyError",
})
