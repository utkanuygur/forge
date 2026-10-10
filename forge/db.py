"""SQLite access layer. Plain sync functions; one short-lived connection per call (WAL mode)."""

from __future__ import annotations

import hashlib
import json
import os
import secrets as _secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
_initialized: set[str] = set()

JSON_COLUMNS = {"lessons", "tools", "http_tools", "seed_files", "payload", "summary", "draft_config"}

# Runs that belong to a draft playground session: excluded from run lists, metrics and improvement.
NOT_DRAFT = "(session_id IS NULL OR session_id NOT IN (SELECT id FROM sessions WHERE version_id IS NULL))"


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent column migrations for databases created before a column existed."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if "session_id" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN session_id TEXT")
    if "turn_index" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN turn_index INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS runs_session ON runs(session_id, turn_index)")
BOOL_COLUMNS = {"auto_promote", "network", "passed"}

# ---------------------------------------------------------------- basics


def data_dir() -> Path:
    path = Path(os.environ.get("FORGE_DATA_DIR") or "./data").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_dir(run_id: str) -> Path:
    return data_dir() / "runs" / run_id


def new_id(prefix: str) -> str:
    return f"{prefix}_{_secrets.token_hex(6)}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> str:
    return "fk_" + _secrets.token_urlsafe(24)


def now() -> float:
    return time.time()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = str(data_dir() / "forge.db")
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path not in _initialized:
        conn.executescript(SCHEMA)
        _migrate(conn)
        _initialized.add(path)
    try:
        yield conn
    finally:
        conn.close()


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for k, v in out.items():
        if k in JSON_COLUMNS and isinstance(v, str):
            out[k] = json.loads(v)
        elif k in BOOL_COLUMNS and v is not None:
            out[k] = bool(v)
    return out


def _encode(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, bool):
        return int(value)
    return value


def _one(sql: str, *args: Any) -> dict[str, Any] | None:
    with connect() as c:
        return _decode(c.execute(sql, args).fetchone())


def _all(sql: str, *args: Any) -> list[dict[str, Any]]:
    with connect() as c:
        return [_decode(r) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]


def _insert(table: str, row: dict[str, Any], *, replace: bool = False) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    with connect() as c:
        c.execute(f"{verb} INTO {table} ({cols}) VALUES ({marks})", [_encode(v) for v in row.values()])


def _update(table: str, key: str, key_value: Any, fields: dict[str, Any]) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE {table} SET {sets} WHERE {key} = ?", [*map(_encode, fields.values()), key_value])


# ---------------------------------------------------------------- agents


def create_agent(slug: str, name: str, description: str = "", auto_promote: bool = False) -> tuple[dict, str]:
    """Returns (agent row, plaintext api key)."""
    key = new_api_key()
    row = {
        "id": new_id("ag"), "slug": slug, "name": name, "description": description, "prod_version_id": None,
        "auto_promote": auto_promote, "api_key_hash": hash_key(key),
        "share_token": _secrets.token_urlsafe(16), "created_at": now(),
    }
    _insert("agents", row)
    return get_agent(row["id"]), key  # type: ignore[return-value]


def get_agent(agent_id: str) -> dict | None:
    return _one("SELECT * FROM agents WHERE id = ?", agent_id)


def get_agent_by_slug(slug: str) -> dict | None:
    return _one("SELECT * FROM agents WHERE slug = ?", slug)


def list_agents() -> list[dict]:
    return _all("SELECT * FROM agents ORDER BY created_at")


def update_agent(agent_id: str, **fields: Any) -> dict | None:
    _update("agents", "id", agent_id, fields)
    return get_agent(agent_id)


def rotate_api_key(agent_id: str) -> str:
    key = new_api_key()
    _update("agents", "id", agent_id, {"api_key_hash": hash_key(key)})
    return key


# ---------------------------------------------------------------- versions

VERSION_FIELDS = ("system_prompt", "lessons", "model", "tools", "http_tools", "network", "seed_files",
                  "parent_id", "created_by", "rationale")


def create_version(agent_id: str, **fields: Any) -> dict:
    """Creates version n = max(n) + 1. `fields` are AgentVersion fields (system_prompt and model required)."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        n = c.execute("SELECT COALESCE(MAX(n), 0) + 1 FROM agent_versions WHERE agent_id = ?", (agent_id,)).fetchone()[0]
        row = {
            "id": new_id("v"), "agent_id": agent_id, "n": n, "lessons": [], "http_tools": [], "network": False,
            "seed_files": {}, "parent_id": None, "created_by": "human", "rationale": "",
            "tools": ["bash", "read_file", "write_file", "edit_file"],
            **{k: v for k, v in fields.items() if k in VERSION_FIELDS and v is not None},
            "created_at": now(),
        }
        c.execute(f"INSERT INTO agent_versions ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                  [_encode(v) for v in row.values()])
        c.execute("COMMIT")
    return get_version(row["id"])  # type: ignore[return-value]


def get_version(version_id: str | None) -> dict | None:
    return _one("SELECT * FROM agent_versions WHERE id = ?", version_id) if version_id else None


def get_version_n(agent_id: str, n: int | str) -> dict | None:
    """`n` is a version number or "prod"."""
    if n == "prod":
        agent = get_agent(agent_id)
        return get_version(agent["prod_version_id"]) if agent else None
    return _one("SELECT * FROM agent_versions WHERE agent_id = ? AND n = ?", agent_id, int(n))


def list_versions(agent_id: str) -> list[dict]:
    return _all("SELECT * FROM agent_versions WHERE agent_id = ? ORDER BY n", agent_id)


def version_numbers(agent_id: str) -> dict[str, int]:
    """version_id -> n for an agent."""
    with connect() as c:
        return {r[0]: r[1] for r in c.execute("SELECT id, n FROM agent_versions WHERE agent_id = ?", (agent_id,))}


# ---------------------------------------------------------------- secrets


def set_secrets(agent_id: str, values: dict[str, str | None]) -> list[str]:
    """Upserts secrets; a null or empty value deletes that name. Returns all secret names."""
    with connect() as c:
        for name, value in values.items():
            if value:
                c.execute("INSERT OR REPLACE INTO secrets (agent_id, name, value) VALUES (?, ?, ?)", (agent_id, name, value))
            else:
                c.execute("DELETE FROM secrets WHERE agent_id = ? AND name = ?", (agent_id, name))
    return sorted(get_secrets(agent_id))


def get_secrets(agent_id: str) -> dict[str, str]:
    with connect() as c:
        return {r[0]: r[1] for r in c.execute("SELECT name, value FROM secrets WHERE agent_id = ?", (agent_id,))}


# ---------------------------------------------------------------- runs


def create_run(agent_id: str, version_id: str, source: str, input: str, *, status: str = "queued",
               eval_case_id: str | None = None, callback_url: str | None = None,
               session_id: str | None = None, turn_index: int | None = None) -> dict:
    t = now()
    row = {
        "id": new_id("r"), "agent_id": agent_id, "version_id": version_id, "source": source, "input": input,
        "status": status, "eval_case_id": eval_case_id, "callback_url": callback_url, "created_at": t,
        "started_at": t if status == "running" else None,
    }
    if session_id:
        row.update(session_id=session_id, turn_index=turn_index)
    _insert("runs", row)
    return get_run(row["id"])  # type: ignore[return-value]


def get_run(run_id: str) -> dict | None:
    return _one("SELECT * FROM runs WHERE id = ?", run_id)


def list_runs(agent_id: str, limit: int = 50, source: str | None = None) -> list[dict]:
    if source:
        return _all(f"SELECT * FROM runs WHERE agent_id = ? AND source = ? AND {NOT_DRAFT} "
                    "ORDER BY created_at DESC LIMIT ?", agent_id, source, limit)
    return _all(f"SELECT * FROM runs WHERE agent_id = ? AND {NOT_DRAFT} ORDER BY created_at DESC LIMIT ?",
                agent_id, limit)


def update_run(run_id: str, **fields: Any) -> dict | None:
    _update("runs", "id", run_id, fields)
    return get_run(run_id)


def fail_interrupted_runs() -> int:
    with connect() as c:
        cur = c.execute("UPDATE runs SET status = 'failed', error = 'interrupted', ended_at = ? "
                        "WHERE status IN ('queued', 'running')", (now(),))
        return cur.rowcount


# ---------------------------------------------------------------- events


def append_event(run_id: str, seq: int, ts: float, type: str, payload: dict) -> bool:
    """Inserts an event; duplicates (same run_id, seq) are ignored. Returns True if inserted."""
    with connect() as c:
        cur = c.execute("INSERT OR IGNORE INTO events (run_id, seq, ts, type, payload) VALUES (?, ?, ?, ?, ?)",
                        (run_id, seq, ts, type, json.dumps(payload)))
        return cur.rowcount > 0


def list_events(run_id: str, after: int = -1) -> list[dict]:
    return _all("SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq", run_id, after)


def next_seq(run_id: str) -> int:
    with connect() as c:
        return c.execute("SELECT COALESCE(MAX(seq), -1) + 1 FROM events WHERE run_id = ?", (run_id,)).fetchone()[0]


def has_event(run_id: str, type: str) -> bool:
    return _one("SELECT 1 AS x FROM events WHERE run_id = ? AND type = ? LIMIT 1", run_id, type) is not None


# ---------------------------------------------------------------- feedback


def create_feedback(run_id: str, rating: int, comment: str = "", source: str = "ui") -> dict:
    row = {"id": new_id("fb"), "run_id": run_id, "rating": rating, "comment": comment, "source": source,
           "created_at": now()}
    _insert("feedback", row)
    return row


def list_feedback(run_id: str) -> list[dict]:
    return _all("SELECT * FROM feedback WHERE run_id = ? ORDER BY created_at", run_id)


def negative_feedback_since(agent_id: str, since: float) -> list[dict]:
    """Negative feedback on the agent's non-eval runs created after `since`, joined with run fields."""
    return _all(
        "SELECT f.*, r.input AS run_input, r.output AS run_output FROM feedback f JOIN runs r ON r.id = f.run_id "
        f"WHERE r.agent_id = ? AND r.source != 'eval' AND {NOT_DRAFT.replace('session_id', 'r.session_id')} "
        "AND f.rating < 0 AND f.created_at > ? ORDER BY f.created_at",
        agent_id, since,
    )


# ---------------------------------------------------------------- playground sessions


def create_session(agent_id: str, base_version_id: str, *, version_id: str | None,
                   draft_config: dict | None = None, title: str = "") -> dict:
    t = now()
    row = {"id": new_id("s"), "agent_id": agent_id, "version_id": version_id, "base_version_id": base_version_id,
           "draft_config": draft_config, "title": title, "created_at": t, "updated_at": t}
    _insert("sessions", row)
    return get_session(row["id"])  # type: ignore[return-value]


def get_session(session_id: str) -> dict | None:
    return _one("SELECT * FROM sessions WHERE id = ?", session_id)


def list_sessions(agent_id: str, limit: int = 50) -> list[dict]:
    return _all("SELECT s.*, (SELECT COUNT(*) FROM runs r WHERE r.session_id = s.id) AS turn_count "
                "FROM sessions s WHERE s.agent_id = ? ORDER BY s.updated_at DESC LIMIT ?", agent_id, limit)


def update_session(session_id: str, **fields: Any) -> dict | None:
    _update("sessions", "id", session_id, fields)
    return get_session(session_id)


def session_runs(session_id: str) -> list[dict]:
    return _all("SELECT * FROM runs WHERE session_id = ? ORDER BY turn_index", session_id)


def is_draft_run(run: dict) -> bool:
    if not run.get("session_id"):
        return False
    s = get_session(run["session_id"])
    return bool(s and s["version_id"] is None)


# ---------------------------------------------------------------- eval cases and results


def create_eval_case(agent_id: str, name: str, input: str, checker_type: str, checker: str, split: str,
                     seed_files: dict[str, str] | None = None) -> dict:
    row = {"id": new_id("ec"), "agent_id": agent_id, "name": name, "input": input, "seed_files": seed_files or {},
           "checker_type": checker_type, "checker": checker, "split": split, "created_at": now()}
    _insert("eval_cases", row)
    return get_eval_case(row["id"])  # type: ignore[return-value]


def get_eval_case(case_id: str) -> dict | None:
    return _one("SELECT * FROM eval_cases WHERE id = ?", case_id)


def list_eval_cases(agent_id: str) -> list[dict]:
    return _all("SELECT * FROM eval_cases WHERE agent_id = ? ORDER BY created_at", agent_id)


def delete_eval_case(case_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM eval_results WHERE case_id = ?", (case_id,))
        c.execute("UPDATE runs SET eval_case_id = NULL WHERE eval_case_id = ?", (case_id,))
        c.execute("DELETE FROM eval_cases WHERE id = ?", (case_id,))


def upsert_eval_result(version_id: str, case_id: str, run_id: str, passed: bool, reason: str) -> None:
    _insert("eval_results", {"version_id": version_id, "case_id": case_id, "run_id": run_id, "passed": passed,
                             "reason": reason, "created_at": now()}, replace=True)


def get_eval_result(version_id: str, case_id: str) -> dict | None:
    return _one("SELECT * FROM eval_results WHERE version_id = ? AND case_id = ?", version_id, case_id)


def list_eval_results(version_id: str) -> list[dict]:
    return _all("SELECT * FROM eval_results WHERE version_id = ? ORDER BY created_at", version_id)


# ---------------------------------------------------------------- improvements


def create_improvement(agent_id: str, base_version_id: str) -> dict:
    row = {"id": new_id("im"), "agent_id": agent_id, "base_version_id": base_version_id, "candidate_version_id": None,
           "status": "running", "summary": {}, "created_at": now(), "ended_at": None}
    _insert("improvements", row)
    return get_improvement(row["id"])  # type: ignore[return-value]


def get_improvement(improvement_id: str) -> dict | None:
    return _one("SELECT * FROM improvements WHERE id = ?", improvement_id)


def list_improvements(agent_id: str) -> list[dict]:
    return _all("SELECT * FROM improvements WHERE agent_id = ? ORDER BY created_at DESC", agent_id)


def update_improvement(improvement_id: str, **fields: Any) -> dict | None:
    _update("improvements", "id", improvement_id, fields)
    return get_improvement(improvement_id)


def fail_interrupted_improvements() -> int:
    with connect() as c:
        cur = c.execute("UPDATE improvements SET status = 'failed', ended_at = ?, "
                        "summary = json_set(summary, '$.error', 'interrupted') WHERE status = 'running'", (now(),))
        return cur.rowcount
