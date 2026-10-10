-- Forge database schema (SQLite, WAL mode). Frozen contract: see CONTRACT.md.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS agents (
  id               TEXT PRIMARY KEY,
  slug             TEXT NOT NULL UNIQUE,
  name             TEXT NOT NULL,
  description      TEXT NOT NULL DEFAULT '',
  prod_version_id  TEXT,
  auto_promote     INTEGER NOT NULL DEFAULT 0,
  api_key_hash     TEXT NOT NULL,
  share_token      TEXT NOT NULL UNIQUE,
  created_at       REAL NOT NULL,
  monthly_budget_usd REAL               -- governance: null = no monthly budget
);

CREATE TABLE IF NOT EXISTS agent_versions (
  id             TEXT PRIMARY KEY,
  agent_id       TEXT NOT NULL REFERENCES agents(id),
  n              INTEGER NOT NULL,
  system_prompt  TEXT NOT NULL,
  lessons        TEXT NOT NULL DEFAULT '[]',   -- JSON list[str]
  model          TEXT NOT NULL,
  tools          TEXT NOT NULL,                -- JSON list[str]
  http_tools     TEXT NOT NULL DEFAULT '[]',   -- JSON list[HttpTool]
  network        INTEGER NOT NULL DEFAULT 0,
  seed_files     TEXT NOT NULL DEFAULT '{}',   -- JSON {path: content}
  parent_id      TEXT,
  created_by     TEXT NOT NULL DEFAULT 'human',
  rationale      TEXT NOT NULL DEFAULT '',
  created_at     REAL NOT NULL,
  approval_policy TEXT NOT NULL DEFAULT '{}', -- governance: JSON, see forge/policy.py
  max_run_cost_usd REAL,                      -- governance: null = no per-run cap
  UNIQUE (agent_id, n)
);

CREATE TABLE IF NOT EXISTS secrets (
  agent_id  TEXT NOT NULL REFERENCES agents(id),
  name      TEXT NOT NULL,
  value     TEXT NOT NULL,
  PRIMARY KEY (agent_id, name)
);

CREATE TABLE IF NOT EXISTS runs (
  id             TEXT PRIMARY KEY,
  agent_id       TEXT NOT NULL REFERENCES agents(id),
  version_id     TEXT NOT NULL REFERENCES agent_versions(id),
  source         TEXT NOT NULL,     -- ui | share | api | eval | local
  input          TEXT NOT NULL,
  status         TEXT NOT NULL,     -- queued | running | awaiting_approval | succeeded | failed | timeout | budget_exceeded
  output         TEXT NOT NULL DEFAULT '',
  error          TEXT,
  artifact_path  TEXT,
  diff_path      TEXT,
  turns          INTEGER NOT NULL DEFAULT 0,
  tokens_in      INTEGER NOT NULL DEFAULT 0,
  tokens_out     INTEGER NOT NULL DEFAULT 0,
  eval_case_id   TEXT,
  callback_url   TEXT,
  created_at     REAL NOT NULL,
  started_at     REAL,
  ended_at       REAL,
  cost_usd       REAL NOT NULL DEFAULT 0   -- governance: estimated spend, updated after each model turn
);
CREATE INDEX IF NOT EXISTS runs_agent ON runs(agent_id, created_at DESC);

CREATE TABLE IF NOT EXISTS events (
  run_id   TEXT NOT NULL REFERENCES runs(id),
  seq      INTEGER NOT NULL,
  ts       REAL NOT NULL,
  type     TEXT NOT NULL,
  payload  TEXT NOT NULL,   -- JSON
  PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS feedback (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES runs(id),
  rating      INTEGER NOT NULL,   -- +1 | -1
  comment     TEXT NOT NULL DEFAULT '',
  source      TEXT NOT NULL,      -- ui | share | api | local
  created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_cases (
  id            TEXT PRIMARY KEY,
  agent_id      TEXT NOT NULL REFERENCES agents(id),
  name          TEXT NOT NULL,
  input         TEXT NOT NULL,
  seed_files    TEXT NOT NULL DEFAULT '{}',  -- JSON {path: content}, merged over the version's seed files
  checker_type  TEXT NOT NULL,               -- shell | judge
  checker       TEXT NOT NULL,               -- shell command, or rubric text
  split         TEXT NOT NULL,               -- train | holdout
  created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_results (
  version_id  TEXT NOT NULL REFERENCES agent_versions(id),
  case_id     TEXT NOT NULL REFERENCES eval_cases(id),
  run_id      TEXT NOT NULL REFERENCES runs(id),
  passed      INTEGER NOT NULL,
  reason      TEXT NOT NULL DEFAULT '',
  created_at  REAL NOT NULL,
  PRIMARY KEY (version_id, case_id)
);

CREATE TABLE IF NOT EXISTS improvements (
  id                    TEXT PRIMARY KEY,
  agent_id              TEXT NOT NULL REFERENCES agents(id),
  base_version_id       TEXT NOT NULL,
  candidate_version_id  TEXT,
  status                TEXT NOT NULL,   -- running | promoted | eligible | rejected | failed
  summary               TEXT NOT NULL DEFAULT '{}',  -- JSON comparison, see CONTRACT.md
  created_at            REAL NOT NULL,
  ended_at              REAL
);


-- Governance: one row per tool call that matched an `ask` rule (forge/approvals.py).
CREATE TABLE IF NOT EXISTS approvals (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  tool_call_id  TEXT NOT NULL,
  name          TEXT NOT NULL,
  args          TEXT NOT NULL DEFAULT '{}',   -- JSON, secrets redacted
  rule          TEXT NOT NULL DEFAULT '{}',   -- JSON, the matched rule
  status        TEXT NOT NULL,                -- pending | approved | denied | expired
  note          TEXT NOT NULL DEFAULT '',
  resolved_by   TEXT,                         -- ui | api | timeout | interrupted
  created_at    REAL NOT NULL,
  expires_at    REAL NOT NULL,
  resolved_at   REAL
);
CREATE INDEX IF NOT EXISTS approvals_run ON approvals(run_id, created_at);
