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

JSON_COLUMNS = {"lessons", "lesson_meta", "tools", "http_tools", "seed_files", "payload", "summary", "approval_policy",
                "args", "rule", "mcp_servers", "callable_agents"}
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


# Columns added after the first schema; CREATE TABLE IF NOT EXISTS does not add them to existing databases.
# Keep in sync with schema.sql (the tools columns exist only here: fresh databases get them right after
# schema.sql runs).
ADDED_COLUMNS = {
    "agents": {"eval_repeats": "INTEGER",
               "monthly_budget_usd": "REAL"},  # governance: null = no monthly budget
    "runs": {"cache_read_tokens": "INTEGER NOT NULL DEFAULT 0", "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
             # The one stored run cost in USD (NULL = unknown price); written after each model turn by
             # forge.governance.RunGovernor and re-priced whenever token counts are written (update_run).
             # Backfilled when added.
             "cost_usd": "REAL",
             # tools: runs started by agent__ tools point at their parent, which rolls up its descendants'
             # usage. child_cost_usd sums the children's stored cost_usd and is never part of the parent's own
             # cost_usd, so totals over runs count each run once.
             "parent_run_id": "TEXT", "child_tokens_in": "INTEGER NOT NULL DEFAULT 0",
             "child_tokens_out": "INTEGER NOT NULL DEFAULT 0", "child_cost_usd": "REAL NOT NULL DEFAULT 0"},
    "eval_results": {"passes": "INTEGER", "runs": "INTEGER"},
    "improvements": {"kind": "TEXT NOT NULL DEFAULT 'improve'"},
    "agent_versions": {"lesson_meta": "TEXT NOT NULL DEFAULT '{}'",
                       "approval_policy": "TEXT NOT NULL DEFAULT '{}'",  # governance, see forge/policy.py
                       "max_run_cost_usd": "REAL",  # governance: null = no per-run cap
                       "mcp_servers": "TEXT NOT NULL DEFAULT '[]'",  # tools: JSON list[McpServer]
                       "callable_agents": "TEXT NOT NULL DEFAULT '[]'"},  # tools: JSON list[str] of agent slugs
}


def _migrate(conn: sqlite3.Connection) -> None:
    added: set[tuple[str, str]] = set()
    for table, cols in ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    added.add((table, col))
                except sqlite3.OperationalError as exc:  # another process added it first
                    if "duplicate column" not in str(exc):
                        raise
    if ("runs", "cost_usd") in added:
        _backfill_run_costs(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS runs_parent ON runs(parent_run_id)")
    # Single-run verdicts from before repeated evals become run 0 of their case.
    conn.execute("INSERT OR IGNORE INTO eval_run_results (version_id, case_id, idx, run_id, passed, reason, created_at) "
                 "SELECT version_id, case_id, 0, run_id, passed, reason, created_at FROM eval_results "
                 "WHERE passes IS NULL")
    conn.execute("UPDATE eval_results SET passes = passed, runs = 1 WHERE passes IS NULL")


def _backfill_run_costs(conn: sqlite3.Connection) -> None:
    """Runs recorded before runs.cost_usd existed get their cost from the same price function."""
    from forge import pricing

    rows = conn.execute("SELECT r.id, r.tokens_in, r.tokens_out, v.model FROM runs r "
                        "LEFT JOIN agent_versions v ON v.id = r.version_id").fetchall()
    conn.executemany("UPDATE runs SET cost_usd = ? WHERE id = ?",
                     [(pricing.cost(r["model"], r["tokens_in"] or 0, r["tokens_out"] or 0), r["id"]) for r in rows])


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
                  "parent_id", "created_by", "rationale", "lesson_meta", "approval_policy", "max_run_cost_usd",
                  "mcp_servers", "callable_agents")


def create_version(agent_id: str, **fields: Any) -> dict:
    """Creates version n = max(n) + 1. `fields` are AgentVersion fields (system_prompt and model required)."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        n = c.execute("SELECT COALESCE(MAX(n), 0) + 1 FROM agent_versions WHERE agent_id = ?", (agent_id,)).fetchone()[0]
        row = {
            "id": new_id("v"), "agent_id": agent_id, "n": n, "lessons": [], "lesson_meta": {}, "http_tools": [],
            "network": False,
            "seed_files": {}, "parent_id": None, "created_by": "human", "rationale": "", "approval_policy": {},
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


def set_lesson_meta(version_id: str, meta: dict) -> None:
    """Provenance metadata is the one thing written after a version is created (its n is needed for it)."""
    _update("agent_versions", "id", version_id, {"lesson_meta": meta})


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
               parent_run_id: str | None = None) -> dict:
    from forge import pricing

    t = now()
    v = get_version(version_id)
    row = {
        "id": new_id("r"), "agent_id": agent_id, "version_id": version_id, "source": source, "input": input,
        "status": status, "eval_case_id": eval_case_id, "callback_url": callback_url, "created_at": t,
        "started_at": t if status == "running" else None,
        # 0.0 for a priced model, NULL for an unknown price (see forge.pricing.cost)
        "cost_usd": pricing.cost(v["model"] if v else None, 0, 0),
    }
    if parent_run_id:
        row["parent_run_id"] = parent_run_id
    _insert("runs", row)
    return get_run(row["id"])  # type: ignore[return-value]


def get_run(run_id: str) -> dict | None:
    return _one("SELECT * FROM runs WHERE id = ?", run_id)


def list_runs(agent_id: str, limit: int = 50, source: str | None = None) -> list[dict]:
    if source:
        return _all("SELECT * FROM runs WHERE agent_id = ? AND source = ? ORDER BY created_at DESC LIMIT ?",
                    agent_id, source, limit)
    return _all("SELECT * FROM runs WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?", agent_id, limit)


def update_run(run_id: str, **fields: Any) -> dict | None:
    """Updates a run. Writing token counts without an explicit `cost_usd` re-prices the run with
    forge.pricing.cost, so the stored runs.cost_usd always matches its tokens and version model."""
    if ("tokens_in" in fields or "tokens_out" in fields) and "cost_usd" not in fields:
        from forge import pricing

        r = _one("SELECT r.tokens_in, r.tokens_out, v.model FROM runs r LEFT JOIN agent_versions v "
                 "ON v.id = r.version_id WHERE r.id = ?", run_id)
        if r is not None:
            fields["cost_usd"] = pricing.cost(r["model"], fields.get("tokens_in", r["tokens_in"]) or 0,
                                              fields.get("tokens_out", r["tokens_out"]) or 0)
    _update("runs", "id", run_id, fields)
    return get_run(run_id)


def run_ancestry(run_id: str, limit: int = 32) -> list[dict]:
    """The run followed by its parent, grandparent and so on (via parent_run_id)."""
    chain: list[dict] = []
    seen: set[str] = set()
    current: str | None = run_id
    while current and current not in seen and len(chain) < limit:
        seen.add(current)
        r = get_run(current)
        if r is None:
            break
        chain.append(r)
        current = r.get("parent_run_id")
    return chain


def list_child_runs(run_id: str) -> list[dict]:
    return _all("SELECT * FROM runs WHERE parent_run_id = ? ORDER BY created_at", run_id)


def add_child_usage(run_id: str, tokens_in: int, tokens_out: int, cost_usd: float = 0.0) -> None:
    """Rolls a finished child's usage (its own plus its descendants') into its parent's child_* fields."""
    with connect() as c:
        c.execute("UPDATE runs SET child_tokens_in = child_tokens_in + ?, child_tokens_out = child_tokens_out + ?, "
                  "child_cost_usd = child_cost_usd + ? WHERE id = ?",
                  (int(tokens_in), int(tokens_out), float(cost_usd or 0), run_id))


def fail_interrupted_runs() -> int:
    with connect() as c:
        cur = c.execute("UPDATE runs SET status = 'failed', error = 'interrupted', ended_at = ? "
                        "WHERE status IN ('queued', 'running', 'awaiting_approval')", (now(),))
        c.execute("UPDATE approvals SET status = 'expired', resolved_by = 'interrupted', resolved_at = ? "
                  "WHERE status = 'pending'", (now(),))
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
        "WHERE r.agent_id = ? AND r.source != 'eval' AND f.rating < 0 AND f.created_at > ? ORDER BY f.created_at",
        agent_id, since,
    )


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
        c.execute("DELETE FROM eval_run_results WHERE case_id = ?", (case_id,))
        c.execute("UPDATE runs SET eval_case_id = NULL WHERE eval_case_id = ?", (case_id,))
        c.execute("DELETE FROM eval_cases WHERE id = ?", (case_id,))


def upsert_eval_result(version_id: str, case_id: str, run_id: str, passed: bool, reason: str,
                       passes: int | None = None, runs: int | None = None) -> None:
    """Per-case summary. `passes`/`runs` default to a single run's verdict."""
    _insert("eval_results", {"version_id": version_id, "case_id": case_id, "run_id": run_id, "passed": passed,
                             "reason": reason, "created_at": now(), "passes": int(passed) if passes is None else passes,
                             "runs": 1 if runs is None else runs}, replace=True)


def add_eval_run_result(version_id: str, case_id: str, idx: int, run_id: str, passed: bool, reason: str) -> None:
    _insert("eval_run_results", {"version_id": version_id, "case_id": case_id, "idx": idx, "run_id": run_id,
                                 "passed": passed, "reason": reason, "created_at": now()}, replace=True)


def list_eval_run_results(version_id: str, case_id: str | None = None) -> list[dict]:
    if case_id:
        return _all("SELECT * FROM eval_run_results WHERE version_id = ? AND case_id = ? ORDER BY idx",
                    version_id, case_id)
    return _all("SELECT * FROM eval_run_results WHERE version_id = ? ORDER BY case_id, idx", version_id)


def get_eval_result(version_id: str, case_id: str) -> dict | None:
    return _one("SELECT * FROM eval_results WHERE version_id = ? AND case_id = ?", version_id, case_id)


def list_eval_results(version_id: str) -> list[dict]:
    return _all("SELECT * FROM eval_results WHERE version_id = ? ORDER BY created_at", version_id)


# ---------------------------------------------------------------- improvements


def create_improvement(agent_id: str, base_version_id: str, kind: str = "improve") -> dict:
    row = {"id": new_id("im"), "agent_id": agent_id, "base_version_id": base_version_id, "candidate_version_id": None,
           "status": "running", "summary": {}, "created_at": now(), "ended_at": None, "kind": kind}
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


# ---------------------------------------------------------------- observability


def set_prod_version(agent_id: str, version_id: str, source: str) -> dict | None:
    """Points the agent's prod at `version_id` and records the promotion (source: create | human | auto)."""
    agent = get_agent(agent_id)
    prev = agent["prod_version_id"] if agent else None
    _update("agents", "id", agent_id, {"prod_version_id": version_id})
    if prev != version_id:
        _insert("promotions", {"id": new_id("pr"), "agent_id": agent_id, "version_id": version_id,
                               "from_version_id": prev, "source": source, "created_at": now()})
    return get_agent(agent_id)


def list_promotions(agent_id: str) -> list[dict]:
    return _all("SELECT * FROM promotions WHERE agent_id = ? ORDER BY created_at", agent_id)


def record_grader_call(agent_id: str, kind: str, model: str, tokens_in: int, tokens_out: int, *,
                       duration_ms: float | None = None, improvement_id: str | None = None,
                       run_id: str | None = None) -> None:
    _insert("grader_calls", {"id": new_id("gc"), "agent_id": agent_id, "improvement_id": improvement_id,
                             "run_id": run_id, "kind": kind, "model": model, "tokens_in": tokens_in,
                             "tokens_out": tokens_out, "duration_ms": duration_ms, "created_at": now()})


def link_improvement_run(improvement_id: str, run_id: str) -> None:
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO improvement_runs (improvement_id, run_id) VALUES (?, ?)",
                  (improvement_id, run_id))
