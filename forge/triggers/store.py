"""Database access for the `triggers` and `trigger_fires` tables (see forge/schema.sql)."""

from __future__ import annotations

import json
from typing import Any

from forge import db

JSON_COLS = ("config", "tokens", "context")


def _row(r: dict | None) -> dict | None:
    if r is None:
        return None
    for k in JSON_COLS:
        if isinstance(r.get(k), str):
            r[k] = json.loads(r[k])
    for k in ("enabled", "test"):
        if k in r and r[k] is not None:
            r[k] = bool(r[k])
    return r


def create(agent_id: str, kind: str, config: dict, *, secret: str | None, tokens: dict[str, str],
           enabled: bool = True, last_slot_at: float | None = None) -> dict:
    t = db.now()
    row = {"id": db.new_id("tr"), "agent_id": agent_id, "kind": kind, "config": json.dumps(config), "secret": secret,
           "tokens": json.dumps(tokens), "enabled": int(enabled), "created_at": t,
           "last_slot_at": t if last_slot_at is None else last_slot_at}
    with db.connect() as c:
        c.execute(f"INSERT INTO triggers ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
    return get(row["id"])  # type: ignore[return-value]


def get(trigger_id: str) -> dict | None:
    with db.connect() as c:
        r = c.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    return _row(dict(r) if r else None)


def list_for_agent(agent_id: str) -> list[dict]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM triggers WHERE agent_id = ? ORDER BY created_at", (agent_id,)).fetchall()
    return [_row(dict(r)) for r in rows]  # type: ignore[misc]


def list_enabled(kind: str) -> list[dict]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM triggers WHERE kind = ? AND enabled = 1", (kind,)).fetchall()
    return [_row(dict(r)) for r in rows]  # type: ignore[misc]


def update(trigger_id: str, **fields: Any) -> dict | None:
    if fields:
        enc = {k: json.dumps(v) if k in JSON_COLS else int(v) if isinstance(v, bool) else v for k, v in fields.items()}
        with db.connect() as c:
            c.execute(f"UPDATE triggers SET {', '.join(f'{k} = ?' for k in enc)} WHERE id = ?",
                      [*enc.values(), trigger_id])
    return get(trigger_id)


def claim_slot(trigger_id: str, slot: float) -> bool:
    """Atomically advances last_slot_at to `slot`; False if another tick (or process) already handled it."""
    with db.connect() as c:
        cur = c.execute("UPDATE triggers SET last_slot_at = ? WHERE id = ? AND enabled = 1 AND "
                        "(last_slot_at IS NULL OR last_slot_at < ?)", (slot, trigger_id, slot))
        return cur.rowcount == 1


def delete(trigger_id: str) -> None:
    with db.connect() as c:
        c.execute("DELETE FROM trigger_fires WHERE trigger_id = ?", (trigger_id,))
        c.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,))


def add_fire(trigger_id: str, status: str, *, run_id: str | None = None, detail: str = "", test: bool = False,
             context: dict | None = None) -> dict:
    row = {"id": db.new_id("tf"), "trigger_id": trigger_id, "run_id": run_id, "status": status, "detail": detail,
           "test": int(test), "context": json.dumps(context or {}), "created_at": db.now()}
    with db.connect() as c:
        c.execute(f"INSERT INTO trigger_fires ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                  list(row.values()))
    return get_fire(row["id"])  # type: ignore[return-value]


def get_fire(fire_id: str) -> dict | None:
    with db.connect() as c:
        r = c.execute("SELECT * FROM trigger_fires WHERE id = ?", (fire_id,)).fetchone()
    return _row(dict(r) if r else None)


def update_fire(fire_id: str, **fields: Any) -> None:
    with db.connect() as c:
        c.execute(f"UPDATE trigger_fires SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                  [*fields.values(), fire_id])


def list_fires(trigger_id: str, limit: int = 20) -> list[dict]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM trigger_fires WHERE trigger_id = ? ORDER BY created_at DESC, rowid DESC "
                         "LIMIT ?", (trigger_id, limit)).fetchall()
    return [_row(dict(r)) for r in rows]  # type: ignore[misc]
