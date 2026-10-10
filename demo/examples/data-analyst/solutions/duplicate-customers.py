"""Reference: duplicate accounts by trimmed, lowercased email."""

import sys
from collections import defaultdict

from _data import rows, workspace, write


def normalize(email: str) -> str:
    return email.strip().lower()


if "--gmail" in sys.argv:  # used by the naive variant
    def normalize(email: str) -> str:  # noqa: F811
        local, _, domain = email.strip().lower().partition("@")
        return (local.replace(".", "") if domain == "gmail.com" else local) + "@" + domain

ws = workspace()
groups = defaultdict(list)
for c in rows(ws, "customers.csv"):
    groups[normalize(c["email"])].append(c["customer_id"])
dups = sorted(sorted(g) for g in groups.values() if len(g) > 1)
write(ws, "results.json", {"duplicate_groups": dups, "extra_records": sum(len(g) - 1 for g in dups)})
