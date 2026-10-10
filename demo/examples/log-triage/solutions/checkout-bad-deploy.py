"""Reference: find the checkout outage from the access, app and deploy logs."""

import re
from collections import Counter

from _logs import access, app, iso, workspace, write

ws = workspace()
rows = access(ws)
errors = [r for r in rows if r["status"] >= 500]
endpoint = Counter((r["method"], r["path"].split("?")[0]) for r in errors).most_common(1)[0][0]
hits = [r for r in errors if (r["method"], r["path"].split("?")[0]) == endpoint]
exc = Counter(e["exc_type"] for e in app(ws) if e.get("exc_type")).most_common(1)[0][0]
deploys = (ws / "logs/deploy/deploy.log").read_text().splitlines()
bad = next(re.search(r"version=(\S+)", d).group(1) for d in deploys
           if "service=checkout-api" in d and "action=start" in d)
fixed = next(re.search(r"version=(\S+)", d).group(1) for d in deploys if "action=rollback_finish" in d)
write(ws, "answer.json", {
    "endpoint": f"{endpoint[0]} {endpoint[1]}",
    "incident_start": iso(min(r["t"] for r in hits)),
    "incident_end": iso(max(r["t"] for r in hits)),
    "status_codes": sorted({r["status"] for r in hits}),
    "failed_requests": len(hits),
    "bad_version": bad,
    "rollback_version": fixed,
    "exception": exc,
})
