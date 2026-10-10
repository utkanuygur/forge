# Forge contract (frozen)

Shared by every component. Python types live in `forge/types.py`, the database in `forge/schema.sql`.
Change any of these only with a note in this file and updates to every implementer.

## Repository layout and ownership

| Path | Owner | Contents |
|---|---|---|
| `forge/types.py`, `forge/schema.sql`, `CONTRACT.md` | lead | Frozen contract |
| `forge/providers.py` | runtime | `make_provider(model)`, Anthropic and OpenAI-compatible adapters, `FakeProvider` for tests |
| `forge/loop.py` | runtime | `run_agent(...)`, tool definitions, truncation, redaction |
| `forge/executors.py` | runtime | `DockerExecutor`, `LocalExecutor`, `make_executor(...)` |
| `sandbox/Dockerfile` | runtime | `forge-sandbox` image |
| `forge/db.py` | server | SQLite access layer (sync `sqlite3`, called via `asyncio.to_thread` where needed) |
| `forge/server/*.py` | server | FastAPI app `forge.server.app:app`, run manager, SSE |
| `forge/improve.py` | improve | Eval runner, judge, optimizer, promotion rule |
| `forge/cli.py` | cli | `forge` Typer app |
| `web/` | web | React + Vite + Tailwind single-page app |
| `demo/` | demo | `acme-orders` seed repo, `repo-maintainer` agent definition, eval cases |
| `tests/` | each owner | `tests/test_<area>.py` |

## Conventions

- IDs: prefixed random hex, `ag_`, `v_`, `r_`, `fb_`, `ec_`, `im_` + 12 hex chars.
- Times: unix seconds as float.
- Model strings: `anthropic:<model-id>` or `openai_compat:<base_url>|<model>`, e.g. `openai_compat:http://localhost:11434/v1|qwen3:8b`.
- Data directory: `$FORGE_DATA_DIR` (default `./data`): `forge.db`, `runs/<run_id>/workspace.tar.gz`, `runs/<run_id>/diff.patch`.
- Secrets are redacted in event payloads by replacing each secret value with `[REDACTED]` before `emit`.
- Default models: `$FORGE_DEFAULT_MODEL` for agents, `$FORGE_GRADER_MODEL` for judge and optimizer.

## HTTP API (prefix `/v1`)

Auth for runtime endpoints: `Authorization: Bearer <api_key>` or `?share=<share_token>` (also header `X-Share-Token`).
Builder endpoints are unauthenticated (local builder). Errors: `{"detail": "<message>"}` with a 4xx/5xx status.

### Objects

```jsonc
// Agent
{"id": "ag_..", "slug": "repo-maintainer", "name": "Repo maintainer", "description": "",
 "prod_version": 1, "auto_promote": false, "share_token": "..", "created_at": 0.0,
 "versions": [Version, ...]}            // versions only on GET /agents/{slug}

// Version  (AgentVersion fields, JSON)
{"id": "v_..", "agent_id": "ag_..", "n": 1, "system_prompt": "..", "lessons": ["..."],
 "model": "anthropic:claude-sonnet-5-5", "tools": ["bash","read_file","write_file","edit_file"],
 "http_tools": [], "network": false, "seed_files": {"path": "content"},
 "parent_id": null, "created_by": "human", "rationale": "", "created_at": 0.0}

// Run
{"id": "r_..", "agent_id": "ag_..", "version_id": "v_..", "version_n": 1, "source": "api",
 "input": "..", "status": "succeeded", "output": "..", "error": null,
 "turns": 7, "tokens_in": 0, "tokens_out": 0, "has_artifacts": true,
 "created_at": 0.0, "started_at": 0.0, "ended_at": 0.0,
 "feedback": [Feedback, ...]}

// Event  (forge.types.Event)
{"run_id": "r_..", "seq": 0, "ts": 0.0, "type": "tool_call", "payload": {...}}

// Feedback
{"id": "fb_..", "run_id": "r_..", "rating": -1, "comment": "..", "source": "ui", "created_at": 0.0}

// EvalCase
{"id": "ec_..", "agent_id": "ag_..", "name": "add-refund-endpoint", "input": "..",
 "seed_files": {}, "checker_type": "shell", "checker": "make check && ! grep -rn 'print(' src",
 "split": "train", "created_at": 0.0}

// Improvement (comparison summary)
{"id": "im_..", "agent_id": "ag_..", "status": "eligible",   // running|promoted|eligible|rejected|failed
 "base_version": 1, "candidate_version": 2,
 "base":      {"train": {"passed": 1, "total": 4}, "holdout": {"passed": 1, "total": 3}},
 "candidate": {"train": {"passed": 4, "total": 4}, "holdout": {"passed": 3, "total": 3}},
 "cases": [{"case_id": "ec_..", "name": "..", "split": "train",
            "base": {"passed": false, "reason": "..", "run_id": "r_.."},
            "candidate": {"passed": true, "reason": "..", "run_id": "r_.."}}],
 "newly_passing": ["ec_.."], "regressions": [],
 "lessons_diff": [{"op": "add", "text": "..", "evidence_run_ids": ["r_.."], "rationale": ".."}],
 "error": null, "created_at": 0.0, "ended_at": 0.0}
```

### Builder

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/agents` | `{slug, name, description?, system_prompt, model?, tools?, network?, seed_files?, auto_promote?}` | `Agent` + `"api_key"` (shown once) |
| GET | `/agents` | | `[Agent]` |
| GET | `/agents/{slug}` | | `Agent` with `versions` |
| PATCH | `/agents/{slug}` | `{name?, description?, auto_promote?}` | `Agent` |
| POST | `/agents/{slug}/versions` | `{system_prompt?, lessons?, model?, tools?, network?, seed_files?, rationale?}` (unset fields copied from prod) | `Version` |
| GET | `/agents/{slug}/versions/{n or "prod"}` | | `Version` |
| POST | `/agents/{slug}/promote` | `{version: n}` | `Agent` |
| PUT | `/agents/{slug}/secrets` | `{NAME: value, ...}` | `{"names": [..]}` |
| POST | `/agents/{slug}/rotate-key` | | `{"api_key": ".."}` |
| GET | `/templates` | | `[{key, name, description, system_prompt, tools, network}]` |

### Runtime (API key or share token)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/agents/{slug}/runs` | JSON `{input, version?, wait?, callback_url?, seed_files?}` or multipart `input`, `version?`, `wait?`, `callback_url?`, `files` (each file placed at its filename; a single `.tar.gz` is extracted) | `Run` (final if `wait=true`) |
| GET | `/agents/{slug}/runs?limit=50` | | `[Run]` newest first |
| GET | `/runs/{id}` | | `Run` |
| GET | `/runs/{id}/events?after=<seq>` | | SSE stream: each message `event: <type>`, `id: <seq>`, `data: <Event JSON>`; ends with `event: end` after `run_finished` |
| GET | `/runs/{id}/events.json` | | `[Event]` |
| GET | `/runs/{id}/artifacts` | | `application/gzip` |
| GET | `/runs/{id}/diff` | | `text/plain` |
| POST | `/runs/{id}/feedback` | `{rating: 1 or -1, comment?}` | `Feedback` |

Builder UI calls runtime endpoints without auth when the request comes from localhost.

Auth amendments (server implementation):
- Builder routes require a localhost client or that agent's API key; `GET/POST /agents` are localhost only; `/templates` is open; `FORGE_TRUST_ALL=1` disables checks.
- A share token may `GET /agents/{slug}` and its versions (for the share page) and start/read its own share runs; it gets 401 on builder, eval and improve routes. With a share token, `GET /agents/{slug}/runs` lists only share-sourced runs.
- Share page URL: `/s/{slug}?share=<token>`.
- Extras: Run includes `eval_case_id`; eval results include `"running": bool`; `PUT secrets` with an empty value deletes it.

### Improvement

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/agents/{slug}/evals` | | `[EvalCase]` |
| POST | `/agents/{slug}/evals` | `{name, input, seed_files?, checker_type, checker, split}` | `EvalCase` |
| DELETE | `/evals/{id}` | | `{"ok": true}` |
| POST | `/runs/{id}/save-as-eval` | `{name?, checker_type?, checker?, split?}` (default judge rubric from feedback comment) | `EvalCase` |
| POST | `/agents/{slug}/evals/run` | `{version?: n}` | `{"version": n, "results": [...]}` runs (cached) eval of one version, async; poll via GET below |
| GET | `/agents/{slug}/evals/results?version=n` | | `{"version": n, "train": {...}, "holdout": {...}, "results": [{case_id, passed, reason, run_id}]}` |
| POST | `/agents/{slug}/improve` | `{}` | `Improvement` (status `running`; poll) |
| GET | `/improvements/{id}` | | `Improvement` |
| GET | `/agents/{slug}/improvements` | | `[Improvement]` newest first |

### Local runner

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/runs` | `{agent_slug, version_n, input, source: "local"}` | `Run` (status `running`) |
| POST | `/runs/{id}/events` | `[Event]` | `{"ok": true}` (a `run_finished` event finalizes the run) |

## Promotion rule

Candidate eligible iff `candidate_passed_total > base_passed_total` and no case passed by base fails for candidate.
`auto_promote` agents: eligible candidates are promoted automatically; an improvement cycle also starts when 3 new
negative feedback items arrive since the last improvement.

## Governance: approvals and spending caps (additive, feat/governance)

All changes are additive; an agent without a policy, cap or budget behaves exactly as before.
Logic lives in `forge/policy.py` (rule matching), `forge/budget.py` (pricing and month-to-date spend),
`forge/governance.py` (the `RunGovernor` hooks and the approval Future registry) and
`forge/server/governance.py` (routes).

### New fields

| Object | Field | Notes |
|---|---|---|
| Version | `approval_policy` | `{"rules": [Rule], "timeout_seconds": 900}`; `{}` means no rules. Copied from prod into new versions and optimizer candidates. Invalid policies are a 400. |
| Version | `max_run_cost_usd` | float or null (no cap). Explicit `null` in `POST /versions` removes the cap. |
| Agent | `monthly_budget_usd` | float or null (no budget). Set on `POST /agents` or `PATCH /agents/{slug}` (explicit `null` removes it). |
| Agent | `month_to_date_usd`, `budget_state`, `pending_approvals` | read only; `budget_state` is `none`, `ok`, `warning` (at 80%) or `exceeded` |
| Run | `cost_usd` | estimated spend, updated after every model turn |

Rule: `{"tool": "bash"|"read_file"|"write_file"|"edit_file"|"*", "pattern"?: str, "mode": "ask"|"deny", "label"?: str}`.
For `bash` the pattern is a regular expression searched in `cmd`; for file tools it is a path glob (`*` within
a directory, `**` across directories, a glob without `/` also matches the file name anywhere). No pattern
matches every call to the tool; `*` cannot have a pattern. A matching `deny` wins, otherwise the first
matching `ask`. Database: additive columns `agents.monthly_budget_usd`, `agent_versions.approval_policy`,
`agent_versions.max_run_cost_usd`, `runs.cost_usd` (added to existing databases on startup by
`db.ADDED_COLUMNS`) and a new `approvals` table.

### New run statuses

| Status | Terminal | Meaning |
|---|---|---|
| `awaiting_approval` | no | Paused before a tool call that matched an `ask` rule; the sandbox stays alive. Server restart marks it `failed` with error `interrupted` (pending approvals become `expired`). |
| `budget_exceeded` | yes | Cumulative estimated cost went over the version's `max_run_cost_usd`. |

### New events

| Type | Payload |
|---|---|
| `approval_requested` | `{id, tool_call_id, name, args, rule: {index, tool, pattern, mode, label, description}, expires_at}` |
| `approval_resolved` | `{id, tool_call_id, decision: "approve" or "deny", note, by: "ui", "api" or "timeout"}` |
| `budget_warning` | `{scope: "run" or "month", limit_usd, spent_usd, fraction}`; once per scope per run, at 80% |

Order for an `ask` call: `tool_call`, `approval_requested`, `approval_resolved`, `tool_result`. Approve executes
the call. Deny, or no decision before `timeout_seconds` (default 15 minutes), returns an `is_error` tool result
`Denied by reviewer: <note>` and the run continues. A `deny` rule returns `Denied by policy: <rule>` at once with
no approval events. Time spent waiting does not count toward the run's wall-clock limit, but a paused run
keeps its slot in the run concurrency limit. Exceeding the run cap emits `error`
`Run stopped: per-run spending cap of $X exceeded ($Y spent).` and ends `budget_exceeded`. Policies apply to
every hosted run, eval runs included.

### New endpoints

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| GET | `/runs/{id}/approvals` | runtime (share allowed) | | `[Approval]` |
| POST | `/runs/{id}/approvals/{approval_id}` | localhost builder or API key; share token gets 403 | `{decision: "approve" or "deny", note?}` | `Approval`; 404 unknown, 409 no longer pending |
| GET | `/agents/{slug}/governance` | builder | | `{monthly_budget_usd, month_to_date_usd, fraction, state, pending_approvals, awaiting_runs: [run_id]}` |

`Approval`: `{id: "ap_..", run_id, tool_call_id, name, args, rule, status: "pending"|"approved"|"denied"|"expired",
note, resolved_by, created_at, expires_at, resolved_at}`.

Spending: month-to-date spend is the sum of `cost_usd` of the agent's runs created since the first of the
calendar month (UTC), eval runs included. When it is at or over `monthly_budget_usd`, `POST /agents/{slug}/runs`,
`POST /agents/{slug}/evals/run` and `POST /agents/{slug}/improve` return **402** `{"detail": "Monthly budget of $X
reached ..."}`; an improvement already running stops starting eval runs and ends `failed` with that reason.
Prices (USD per million input and output tokens) come from `forge.pricing` when present, else the fallback table
in `forge/budget.py` (claude-opus-5-5 4/20, claude-sonnet-5-5 2/10, claude-haiku-5-5 0.10/0.50; `fake:` and
`openai_compat:` free); `FORGE_PRICES='{"<model>": [in, out]}'` overrides both. Unknown models are not priced.

Webhook: when a run with a `callback_url` pauses, the URL also receives the Run JSON (status
`awaiting_approval`) with an extra `approval` field. The final Run JSON is posted as before.

Runtime: `run_agent(..., hooks=None)` accepts an optional hooks object with `after_turn(result, send)` and
`before_tool(call, send)`; the run manager passes `forge.governance.RunGovernor` when `run_agent` accepts it.
Local CLI runs are not governed.
