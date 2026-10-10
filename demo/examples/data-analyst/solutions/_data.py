"""CSV helpers shared by the data-analyst reference solutions (stdlib only, reads only the seed files)."""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


def workspace() -> Path:
    return Path(sys.argv[1])


def rows(ws: Path, name: str) -> list[dict]:
    with open(ws / "data" / name, newline="") as f:
        return list(csv.DictReader(f))


def cents(price: str) -> int:
    whole, frac = price.split(".")
    return int(whole) * 100 + int(frac)


def month_add(month: str, k: int) -> str:
    y, m = map(int, month.split("-"))
    m += k
    y += (m - 1) // 12
    return f"{y}-{(m - 1) % 12 + 1:02d}"


def revenue_by_region_month(ws: Path, statuses: tuple[str, ...] = ("completed",)) -> dict[tuple[str, str], int]:
    region = {c["customer_id"]: c["region"] for c in rows(ws, "customers.csv")}
    rev: dict[tuple[str, str], int] = defaultdict(int)
    for o in rows(ws, "orders.csv"):
        if o["status"] in statuses:
            rev[(region[o["customer_id"]], o["order_date"][:7])] += int(o["quantity"]) * cents(o["unit_price"])
    return rev


def write(ws: Path, name: str, obj: object) -> None:
    (ws / name).write_text(json.dumps(obj, indent=2) + "\n")
