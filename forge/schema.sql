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
  eval_repeats     INTEGER,           -- runs per eval case (k); NULL = $FORGE_EVAL_REPEATS or 3
  monthly_budget_usd REAL,            -- governance: null = no monthly budget
  api_key_hash     TEXT NOT NULL,
  share_token      TEXT NOT NULL UNIQUE,
  created_at       REAL NOT NULL
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
  lesson_meta    TEXT NOT NULL DEFAULT '{}',   -- JSON {lesson text: provenance}, see CONTRACT.md
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
  cache_read_tokens   INTEGER NOT NULL DEFAULT 0,  -- prompt-cache reads (not included in tokens_in)
  cache_write_tokens  INTEGER NOT NULL DEFAULT 0,  -- prompt-cache writes (not included in tokens_in)
  cost_usd       REAL,              -- USD from forge.pricing.cost, updated after each model turn; NULL = unknown price
  eval_case_id   TEXT,
  callback_url   TEXT,
  created_at     REAL NOT NULL,
  started_at     REAL,
  ended_at       REAL
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

-- Per-case verdict for a version: majority of its k repeated runs (see eval_run_results).
-- run_id/reason are a representative run: a failing one if the case failed, else a passing one.
CREATE TABLE IF NOT EXISTS eval_results (
  version_id  TEXT NOT NULL REFERENCES agent_versions(id),
  case_id     TEXT NOT NULL REFERENCES eval_cases(id),
  run_id      TEXT NOT NULL REFERENCES runs(id),
  passed      INTEGER NOT NULL,
  reason      TEXT NOT NULL DEFAULT '',
  created_at  REAL NOT NULL,
  passes      INTEGER,   -- passing runs among `runs` (NULL only in pre-migration rows)
  runs        INTEGER,   -- k: runs the verdict is based on
  PRIMARY KEY (version_id, case_id)
);

-- One row per repeated eval run (idx 0..k-1).
CREATE TABLE IF NOT EXISTS eval_run_results (
  version_id  TEXT NOT NULL REFERENCES agent_versions(id),
  case_id     TEXT NOT NULL REFERENCES eval_cases(id),
  idx         INTEGER NOT NULL,
  run_id      TEXT NOT NULL REFERENCES runs(id),
  passed      INTEGER NOT NULL,
  reason      TEXT NOT NULL DEFAULT '',
  created_at  REAL NOT NULL,
  PRIMARY KEY (version_id, case_id, idx)
);

CREATE TABLE IF NOT EXISTS improvements (
  id                    TEXT PRIMARY KEY,
  agent_id              TEXT NOT NULL REFERENCES agents(id),
  base_version_id       TEXT NOT NULL,
  candidate_version_id  TEXT,
  status                TEXT NOT NULL,   -- running | promoted | eligible | rejected | failed
  summary               TEXT NOT NULL DEFAULT '{}',  -- JSON comparison, see CONTRACT.md
  created_at            REAL NOT NULL,
  ended_at              REAL,
  kind                  TEXT NOT NULL DEFAULT 'improve'  -- improve | prune
);

-- Observability (additive; see CONTRACT.md "Observability").
CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at);

-- Every change of an agent's production pointer (creation, manual promote, auto promote).
CREATE TABLE IF NOT EXISTS promotions (
  id               TEXT PRIMARY KEY,
  agent_id         TEXT NOT NULL REFERENCES agents(id),
  version_id       TEXT NOT NULL,
  from_version_id  TEXT,
  source           TEXT NOT NULL,     -- create | human | auto
  created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS promotions_agent ON promotions(agent_id, created_at);

-- Token usage of judge and optimizer calls (they run outside any agent run).
CREATE TABLE IF NOT EXISTS grader_calls (
  id              TEXT PRIMARY KEY,
  agent_id        TEXT NOT NULL,
  improvement_id  TEXT,              -- set when the call happened inside an improvement cycle
  run_id          TEXT,              -- for judge calls: the eval run being graded
  kind            TEXT NOT NULL,     -- judge | optimizer
  model           TEXT NOT NULL,
  tokens_in       INTEGER NOT NULL DEFAULT 0,
  tokens_out      INTEGER NOT NULL DEFAULT 0,
  duration_ms     REAL,
  created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS grader_calls_agent ON grader_calls(agent_id, created_at);

-- Eval runs started by an improvement cycle (cached results from earlier evals are not included).
CREATE TABLE IF NOT EXISTS improvement_runs (
  improvement_id  TEXT NOT NULL,
  run_id          TEXT NOT NULL,
  PRIMARY KEY (improvement_id, run_id)
);


-- Governance: one row per tool call that matched an `ask` rule (forge/governance.py).
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
