"""Log parsing shared by the log-triage reference solutions (stdlib only, reads only the seed files)."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ACCESS = re.compile(r'^(\S+) - - \[([^\]]+)\] "(\S+) (\S+) [^"]*" (\d{3}) \S+ "[^"]*" "([^"]*)" rt=([\d.]+)')


def workspace() -> Path:
    return Path(sys.argv[1])


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def access(ws: Path) -> list[dict]:
    rows = []
    for line in (ws / "logs/nginx/access.log").read_text().splitlines():
        ip, ts, method, path, status, ua, rt = ACCESS.match(line).groups()  # type: ignore[union-attr]
        rows.append({"ip": ip, "t": datetime.strptime(ts, "%d/%b/%Y:%H:%M:%S %z"), "method": method,
                     "path": path, "status": int(status), "ua": ua, "rt": float(rt)})
    return rows


def app(ws: Path) -> list[dict]:
    return [json.loads(line) for line in (ws / "logs/app/checkout-api.jsonl").read_text().splitlines()]


def write(ws: Path, name: str, obj: object) -> None:
    (ws / name).write_text(json.dumps(obj, indent=2) + "\n")
