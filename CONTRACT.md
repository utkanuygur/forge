# Forge contract (frozen)

Shared by every component. Python types live in `forge/types.py`, the database in `forge/schema.sql`.
Change any of these only with a note in this file and updates to every implementer.

## Repository layout and ownership

| Path | Owner | Contents |
|---|---|---|
| `forge/types.py`, `forge/schema.sql`, `CONTRACT.md` | lead | Frozen contract |
| `forge/providers.py` | runtime | `make_provider(model)`, Anthropic and OpenAI-compatible adapters, `FakeProvider` for tests |
| `forge/loop.py` | runtime | `run_agent(...)`, tool definitions, truncation, redaction |
| `forge/tools/` | tools | Tool registry (`registry.py`) and sources: HTTP tools (`http.py`), MCP servers (`mcp.py`), callable agents (`agents.py`) |
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
 "prod_version": 1, "auto_promote": false, "eval_repeats": 3, "share_token": "..", "created_at": 0.0,
 "versions": [Version, ...]}            // versions only on GET /agents/{slug}

// Version  (AgentVersion fields, JSON)
{"id": "v_..", "agent_id": "ag_..", "n": 1, "system_prompt": "..", "lessons": ["..."],
 "model": "anthropic:claude-sonnet-5-5", "tools": ["bash","read_file","write_file","edit_file"],
 "http_tools": [HttpTool, ...], "mcp_servers": [McpServer, ...], "callable_agents": ["other-agent"],
 "network": false, "seed_files": {"path": "content"},
 "parent_id": null, "created_by": "human", "rationale": "", "created_at": 0.0}

// Run
{"id": "r_..", "agent_id": "ag_..", "version_id": "v_..", "version_n": 1, "source": "api",
 "input": "..", "status": "succeeded", "output": "..", "error": null,
  "turns": 7, "tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
 "has_artifacts": true,
 "parent_run_id": null,                  // set when an agent__ tool started this run
 "child_usage": {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0},  // all descendant runs, not in tokens_in/out
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
            "base": {"passed": false, "reason": "..", "run_id": "r_..", "passes": 1, "runs": 3},
            "candidate": {"passed": true, "reason": "..", "run_id": "r_..", "passes": 3, "runs": 3}}],
 "newly_passing": ["ec_.."], "regressions": [],
 "lessons_diff": [{"op": "add", "text": "..", "evidence_run_ids": ["r_.."], "rationale": ".."}],
 "error": null, "created_at": 0.0, "ended_at": 0.0}
```

### Builder

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/agents` | `{slug, name, description?, system_prompt, model?, tools?, http_tools?, mcp_servers?, callable_agents?, network?, seed_files?, auto_promote?}` | `Agent` + `"api_key"` (shown once) |
| GET | `/agents` | | `[Agent]` |
| GET | `/agents/{slug}` | | `Agent` with `versions` |
| PATCH | `/agents/{slug}` | `{name?, description?, auto_promote?, eval_repeats?: 1..9}` | `Agent` |
| POST | `/agents/{slug}/versions` | `{system_prompt?, lessons?, model?, tools?, http_tools?, mcp_servers?, callable_agents?, network?, seed_files?, rationale?}` (unset fields copied from prod; 400 when the tool configuration is invalid) | `Version` |
| GET | `/agents/{slug}/versions/{n or "prod"}` | | `Version` |
| POST | `/agents/{slug}/promote` | `{version: n}` | `Agent` |
| PUT | `/agents/{slug}/secrets` | `{NAME: value, ...}` | `{"names": [..]}` |
| POST | `/agents/{slug}/rotate-key` | | `{"api_key": ".."}` |
| GET | `/templates` | | `[{key, name, description, system_prompt, tools, network}]` |
| POST | `/agent-drafts` | `{description, examples?: [str], constraints?: str}` | `AgentDraft` (creates nothing; see Authoring) |

Additive: `POST /agents` also accepts `eval_cases?: [{name, input, seed_files?, checker_type, checker, split}]`, created
with the agent (used to create a reviewed draft and its selected starter cases in one call).
| POST | `/agents/{slug}/tools/test` | `{tool: HttpTool, args?, network?}` (network defaults to prod's) | `{output, is_error, status, duration_ms}` (output redacted) |
| POST | `/agents/{slug}/mcp/list` | `{server: McpServer, network?, seed_files?}` (stdio starts a fresh sandbox with these seed files, default prod's) | `{tools: [{name, tool, description, input_schema}]}`; 502 with `detail` when the server cannot be reached |

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
| GET | `/agents/{slug}/evals/results?version=n` | | `{"version": n, "train": {...}, "holdout": {...}, "repeats": k, "results": [{case_id, passed, reason, run_id, passes, runs}]}` |
| POST | `/agents/{slug}/improve` | `{}` | `Improvement` (status `running`; poll) |
| GET | `/improvements/{id}` | | `Improvement` |
| GET | `/agents/{slug}/improvements` | | `[Improvement]` newest first |
| POST | `/agents/{slug}/prune` | `{max_ablations?: 1..30}` (default 8) | `Improvement` with `kind: "prune"` (status `running`; poll) |

### Local runner

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/runs` | `{agent_slug, version_n, input, source: "local"}` | `Run` (status `running`) |
| POST | `/runs/{id}/events` | `[Event]` | `{"ok": true}` (a `run_finished` event finalizes the run) |

## Repeated evals (amendment)

Each eval case runs `k` times per version, `k` = the agent's `eval_repeats` (PATCH, 1..9); unset agents use
`$FORGE_EVAL_REPEATS`, else 3. A case **passes for a version iff a strict majority of its k runs pass**
(`2 * passes > k`; for even k a tie fails). Per-run verdicts are stored in `eval_run_results`; `eval_results`
is the per-case summary with `passes`, `runs` (= k) and a representative `run_id`/`reason`: the first failing
run when the case failed, else the first passing run. Summaries are cached per version: a case whose summary
already covers k runs is not re-run, and changing k reuses stored runs and only adds missing ones. A run whose
checker never ran (infrastructure error) is not stored and is retried on the next evaluation. At most 4 eval
sandbox runs execute at once across all cases and repeats. Eval result and Improvement case objects gain
`passes` and `runs` (additive); `passed` is the majority verdict.

## Stop reasons and prompt caching (amendment)

`ModelResponse` gains `stop_reason` (`end_turn`, `tool_use`, `max_tokens`, `refusal`, ...; OpenAI-compatible
`finish_reason` is mapped: `length` -> `max_tokens`, `content_filter` -> `refusal`, `stop` -> `end_turn`,
`tool_calls` -> `tool_use`), `stop_detail` (refusal category, else its explanation), `cache_read_tokens` and
`cache_write_tokens`. In `run_agent`: a `refusal` turn fails the run with an `error` event
`Model declined: <detail>` and none of its tool calls run; a `max_tokens` turn without tool calls fails the run
(the truncated text is never the final answer); a `max_tokens` turn with tool calls runs all but the last call,
which gets an error result because its arguments may be cut off. `AnthropicProvider` sets `cache_control`
(ephemeral) on the system block, which caches tools + system, and in tool-using calls on the last message block,
which caches the growing conversation (2 of 4 breakpoints). `run_finished` payloads and Run objects carry
`cache_read_tokens` and `cache_write_tokens`; `tokens_in` stays the uncached input count.

## Authoring (additive)

### Agent drafts

`POST /v1/agent-drafts` (localhost builder only) calls `FORGE_GRADER_MODEL` once with the drafting prompt
(`forge/drafting.py`, `DRAFT_SYSTEM`). Claude models get structured output (`output_config.format`, JSON schema
`DRAFT_SCHEMA`); other providers get the same prompt and the reply is parsed leniently. The draft is validated and,
if invalid, the call is retried once as a fresh request listing the problems. Still invalid: `502`. Empty or
oversized description: `400`. Nothing is stored; the builder edits the draft and creates it with `POST /agents`.

```jsonc
// AgentDraft
{"name": "Issue triager", "slug": "issue-triager",   // slug is made free: "-2", "-3"... if taken
 "description": "..", "system_prompt": "..",
 "tools": ["bash", "read_file", "write_file"],        // subset of the built-in tools
 "network": true, "network_reason": "Needs the GitHub REST API.",
 "suggested_secrets": [{"name": "GITHUB_TOKEN", "why": ".."}],   // names only, never values
 "seed_files_hint": "..",
 "eval_cases": [{"name": "crash-report", "input": "..", "checker_type": "judge", "checker": "PASS only if ..",
                 "split": "train"}]}
```

Validation: slug `^[a-z0-9][a-z0-9-]{0,62}$`; tools non-empty and known; system prompt 300 to 8000 chars;
`network_reason` present; secret names `UPPER_SNAKE_CASE` with a reason; 3 to 6 cases with unique slug names,
non-empty inputs, judge rubrics 60 to 1200 chars, shell checkers 1 to 500 chars; at least one train and one
holdout case and no more than half holdout (target: about two thirds train).

### Lesson pruning

`POST /v1/agents/{slug}/prune` (builder or the agent's API key) returns `400` when production has fewer than 2
lessons or there is no train case, and `409` while any improvement or prune is running. Otherwise it starts a
background job stored as an improvement with `kind: "prune"`:

1. Evaluate production (all cases, cached).
2. Leave-one-out ablation on TRAIN cases for at most `max_ablations` lessons, oldest first (by
   `lesson_meta.created_in_version`, then list order). Each ablation is a version identical to production minus
   one lesson: an existing identical version is reused, otherwise a new unpromoted one is created with
   `created_by: "optimizer"` and `rationale: "ablation"`. A lesson is **unhelpful** when removing it does not
   reduce train passes (ties count), **helpful** when it does, **unknown** (kept) when the ablation could not be
   fully evaluated.
   Every evaluation in a prune job (base, ablations, candidate) uses the repeated-eval path: k runs per case,
   majority verdict, per-version caching, at most 4 concurrent sandbox runs (shared by all ablations of a job).
   Ablations evaluate train cases only; an ablation whose summary covers fewer than k runs is `unknown`.
3. If no lesson is unhelpful the job ends `rejected` with an explanatory `error`. Otherwise ONE candidate removes
   every unhelpful lesson and is evaluated on train and holdout. Cached results of any version with an identical
   configuration are copied first (per-run verdicts and the passes/runs summary, for cases the version has no
   runs of its own), so an ablation identical to the candidate is not re-run; missing runs up to k are then added.
4. **Pruning rule** (`forge.pruning.is_eligible_prune`): eligible iff zero regressions and candidate passes >= base
   passes. A tie is enough because the goal is a smaller prompt at equal quality. Prune candidates are never
   auto-promoted, even for `auto_promote` agents; promote with `POST /agents/{slug}/promote`.

Prune jobs do not reset the negative feedback window used by Improve and auto-improve. Their eval runs and judge calls
are attributed to the job like an improvement cycle's (`improvement_runs`, `grader_calls`), and the agent metrics
`cost.improvements` rows carry `kind`. Draft calls are not metered: no agent exists yet.

Improvement additions (all improvements): `"kind": "improve" | "prune"`, `"ablations": [...]` (prune only):

```jsonc
{"lesson": "..", "version": 3, "verdict": "unhelpful",   // unhelpful | helpful | unknown | not_ablated
 "base_train": {"passed": 2, "total": 2}, "train": {"passed": 2, "total": 2},
 "evidence": "removing this lesson kept train at 2/2", "run_ids": ["r_.."]}
```

For prune jobs `lessons_diff` holds one `{"op": "remove", "text", "evidence_run_ids": <ablation train runs>,
"rationale": <evidence>}` per removed lesson.

### Lesson provenance

Version gains `"lesson_meta": {<lesson text>: Provenance}` (JSON column `agent_versions.lesson_meta`, default `{}`).
Lessons carried over unchanged keep their entry; lessons new in a version get one when the optimizer adds or edits
them (`origin: "optimizer"`) or a human supplies them via `POST /agents` or `POST /agents/{slug}/versions`
(`origin: "human"`). Pruning records its verdict on the base version and on the candidate's kept lessons.

```jsonc
{"created_in_version": 2, "evidence_run_ids": ["r_.."], "origin": "optimizer", "op": "add", "created_at": 0.0,
 "edited_from": "..",                                  // edits only
 "pruning": {"improvement_id": "im_..", "verdict": "helpful", "evidence": "..", "at": 0.0, "version": 4}}
```

`lesson_meta` is the only field written after a version is created (its `n` is needed); configuration fields stay
immutable. Schema additions: `improvements.kind TEXT NOT NULL DEFAULT 'improve'`, `agent_versions.lesson_meta`;
existing databases are migrated on first connect (`forge/db.py`, `ADDED_COLUMNS`).

## Tools

Besides the builtin tools (`bash`, `read_file`, `write_file`, `edit_file`), a version can offer three kinds of tools.
All appear to the model as ordinary tools with JSON schemas, are traced as `tool_call` and `tool_result` events, and
never crash a run: failures and timeouts come back as `is_error` results. The loop reaches them through one seam,
`run_agent(..., extra_tools=ToolRegistry)` (`forge/tools/registry.py`): the registry opens after `run_started`
(failures become `error` events), its specs are appended to the builtin ones, calls it owns are routed to it, and it
closes before `run_finished`. Builtin names always win; a duplicate name is reported and skipped.

```jsonc
// HttpTool
{"name": "get_order", "description": "..", "method": "GET",           // GET | POST | PUT | PATCH | DELETE
 "url": "https://api.example.com/orders/{id}",                       // {param} filled URL-encoded from args
 "headers": {"Authorization": "Bearer {{API_KEY}}"},                 // {{NAME}} = agent secret NAME
 "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
 "query_params": [],            // args always sent in the query string; others: query for GET/DELETE, JSON body otherwise
 "timeout": 30, "max_response_chars": 10000}

// McpServer
{"name": "docs", "transport": "http",           // http (Streamable HTTP) | sse | stdio
 "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer {{DOCS_TOKEN}}"},
 "command": "", "args": [], "env": {"TOKEN": "{{DOCS_TOKEN}}"},     // stdio
 "timeout": 60}                                  // seconds per tool call
```

- **HTTP tools** run in the server process. They need `network: true`; arguments are validated against the schema
  (JSON Schema 2020-12) before any request; a missing secret is an error. Responses: `HTTP <status> <reason>`, a
  blank line, then the body truncated to `max_response_chars`; non-2xx is `is_error` with the status and a 2,000
  character excerpt. Timeouts are capped at 120 s and by the run's remaining wall clock.
- **SSRF protection** (HTTP tools and remote MCP servers): only `http` and `https`; the host is resolved and every
  resolved address must be public (loopback, private, link-local, CGNAT, multicast, reserved and unspecified are
  refused, including IPv4-mapped IPv6). HTTP tool connections are pinned to the checked IP (the Host header and TLS
  SNI keep the name), redirects are followed manually (at most 5) with the same check on every hop, and configured
  headers are dropped when a redirect leaves the original origin. The MCP SDK follows redirects only within the
  endpoint's origin. `FORGE_ALLOW_PRIVATE_HTTP=1` on the server disables the address check (development and tests).
- **MCP servers** use the official MCP Python SDK (`mcp`). Tools are named `mcp__<server>__<tool>` (characters
  outside `[A-Za-z0-9_-]` become `_`, at most 64 characters) with the server's input schemas. Results map text as
  is, images to `[image]`, audio to `[audio]`, embedded resources to their text (else `[resource <uri>]`) and
  resource links to `[resource link: <uri>]`. Remote servers need `network: true` and pass the SSRF check. stdio
  servers run inside the run's sandbox via `docker exec -i` (cwd `/workspace`, the agent's network policy); with
  the local executor they run on the host, in ask mode only after confirmation. A server that fails to start
  emits `error` `{"message", "source": "mcp", "server"}` and the run continues without its tools. Connections close
  at run end.
- **Callable agents**: each slug in `callable_agents` becomes `agent__<slug>` with arguments `{"input": string}` and
  that agent's description. A call starts a child run of the callee's production version through the run manager
  (source `agent`, `parent_run_id` set, wall clock limited to the parent's remaining time) and returns its final
  output plus `[<slug> <status> in child run <id>]`. Calls nest at most 3 levels below the top run, and an agent can
  never call itself or an agent already in its chain (`cycle detected`). Child runs bypass the concurrency
  semaphore because the parent already holds a slot, but never the governance checks: `start_run` refuses a
  child with the callee's monthly budget used up (the tool result is an `is_error` `Monthly budget of $X reached
  ...` message, no child run is created), and the child's RunGovernor applies the callee version's per-run cap
  and approval policy. A child paused on an approval keeps counting against the parent's wall clock. When a
  child finishes, its `tokens_in`/`tokens_out` plus its
  own `child_usage` are added to the parent's `child_usage`; a run's `tokens_in`/`tokens_out` never include
  children, so summing runs does not double count. Callable agents are not offered in `forge run` (local runs);
  there, HTTP tools and MCP servers resolve `{{NAME}}` from the local environment.
- **Validation** on create and new version (400): HTTP tool names match `^[A-Za-z][A-Za-z0-9_-]{0,63}$`, are unique,
  not builtin and not prefixed `mcp__` or `agent__`; schemas are object schemas; URL placeholders are parameters;
  MCP server names match `^[A-Za-z0-9]([A-Za-z0-9-]|_(?!_)){0,31}$` and are unique; stdio needs a command; callable
  agents exist, do not repeat and do not include the agent itself.

Event payload additions: `tool_result` for these tools adds `"source": "http" | "mcp" | "agent"`, plus
`"http_status"` (HTTP), `"mcp_server"` (MCP), and `"child_run_id"`, `"child_agent"`, `"child_status"` (agents).

Database (additive, applied by `forge/db.py` `ADDED_COLUMNS` with `ALTER TABLE` when missing):
`agent_versions.mcp_servers` (JSON), `agent_versions.callable_agents` (JSON), `runs.parent_run_id`,
`runs.child_tokens_in`, `runs.child_tokens_out`, `runs.child_cost_usd` (the children's stored `runs.cost_usd`,
the same price source as everything else, see Governance "Run cost"). A run's own `cost_usd` never includes its
children: metrics, Prometheus and budgets sum `cost_usd` over runs, so each run is counted once (a child counts
toward its own agent's monthly budget, not the caller's).

Governance: approval policies cover these tools too. A rule's `tool` may be `mcp__<server>__<tool>`,
`agent__<slug>`, one of the version's HTTP tool names, or a prefix ending in `*` (`mcp__github__*`, `agent__*`);
its `pattern` is a regular expression searched in the call's arguments as compact JSON with sorted keys.

Known gap: remote MCP connections (http and sse) are address checked when they connect, but the connection is not
pinned to the checked IP the way HTTP tool requests are, so a DNS answer that changes between the check and the
connect (DNS rebinding) is not caught. Use `network: false` or trusted MCP hosts until this is pinned.

## Triggers

Additive (tables `triggers` and `trigger_fires` in `forge/schema.sql`, code in `forge/triggers/`). A trigger starts runs of
one agent. Runs it starts have `source` `schedule`, `github` or `slack` (new sources) and use the production version
unless the trigger's `config.version` pins one. IDs: `tr_` (trigger), `tf_` (fire).

```jsonc
// Trigger
{"id": "tr_..", "agent_id": "ag_..", "kind": "schedule",          // schedule | github | slack
 "config": {...}, "enabled": true, "created_at": 0.0, "last_fired_at": 0.0, "last_run_id": "r_..",
 "last_error": null, "has_secret": false, "has_token": false,
 "hook_path": "/v1/hooks/github/tr_..",                            // null for schedules
 "public_url": null,                                               // $FORGE_PUBLIC_URL if set
 "next_fire_times": [0.0, 0.0, 0.0]}                               // schedules only
// TriggerFire (history)
{"id": "tf_..", "trigger_id": "tr_..", "status": "started",       // started | skipped | missed | rate_limited | error
 "detail": "", "test": false, "run_id": "r_..", "delivery": null,  // null | delivered | failed | skipped
 "delivery_error": null, "created_at": 0.0, "run": Run}
```

Config (unknown keys are rejected; every kind also takes `input_template`, `version` and `max_runs_per_hour`, default
`$FORGE_TRIGGER_RATE_LIMIT` or 30):
- `schedule`: `cron` (5 fields or macros such as `@hourly`), `timezone` (IANA, default UTC), `input_template`
  (required; `{date}`, `{time}`, `{datetime}`, `{weekday}`), `skip_if_running` (default true).
- `github`: `events` (subset of `issues`, `issue_comment`, `pull_request`, `push`), `label`, `command` (default
  `/forge`), `branches` (globs), `pr_actions` (default `opened`, `synchronize`), `comment_back` (default true),
  `api_base` (default `https://api.github.com`). Template fields: `{event} {action} {repo} {number} {title} {body}
  {comment} {url} {issue_url} {pr_url} {sender} {author} {label} {branch} {base} {ref} {commits} {commit_count}
  {compare} {head_sha}`. An empty template uses a default per event.
- `slack`: `input_template` (default `{text}`; also `{user} {channel} {command} {team}`), `response_type`
  (`in_channel` or `ephemeral`), `api_base` (default `https://slack.com/api`).

Templates replace only known `{name}` fields (no format specs or attribute access); `{{` and `}}` are literal braces.

### Trigger builder routes (localhost or the agent's API key; share tokens get 401)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/agents/{slug}/triggers` | | `[Trigger]` |
| POST | `/agents/{slug}/triggers` | `{kind, config, enabled?, signing_secret? (slack), token? (GitHub token or Slack bot token)}` | `Trigger`; GitHub adds `"secret"` (generated, shown once) |
| GET | `/triggers/{id}` | | `Trigger` |
| PATCH | `/triggers/{id}` | `{config? (merged), enabled?, signing_secret?, token? ("" removes)}` | `Trigger` |
| DELETE | `/triggers/{id}` | | `{"ok": true}` |
| POST | `/triggers/{id}/rotate-secret` | | `{"secret": ".."}` (GitHub) |
| POST | `/triggers/{id}/test` | `{input?}` | 202 `{run_id, run_url, input}`; fires once with a sample payload; results are not posted out |
| GET | `/triggers/{id}/history?limit=20` | | `[TriggerFire]` newest first |

Secrets and tokens are never returned after creation.

### Hooks (no auth besides the signature; they work through the public tunnel)

| Method | Path | Verification | Returns |
|---|---|---|---|
| POST | `/hooks/github/{id}` | `X-Hub-Signature-256`: HMAC SHA256 of the raw body with the trigger secret | `ping` 200; matching event 202 `{run_id, run_url}`; ignored or disabled 204 (reason in `X-Forge-Ignored`); bad or missing signature 401 |
| POST | `/hooks/slack/{id}` | `X-Slack-Signature` v0 with `X-Slack-Request-Timestamp`, rejected when more than 5 minutes off | Slash command (form): 200 ephemeral `Working on it` with the run link, final output later POSTed to `response_url`. Events API (JSON): `url_verification` echoes `challenge`; `app_mention` 200 `{ok, run_id}` and a threaded `chat.postMessage` reply when a bot token is set. Bad signature 401 |

Both return 429 with `Retry-After` past `max_runs_per_hour`, 404 for an unknown trigger and 413 above 5 MB.
GitHub comment-back (token set, `comment_back`, issue or PR events) and Slack replies are best effort: 3 retries with
backoff on network errors, 429 and 5xx; failures land in `delivery_error` and the trigger's `last_error`. Events from
bots are ignored to avoid loops; duplicate Slack `event_id`s are ignored. `response_url` must start with
`https://hooks.slack.com/` unless `$FORGE_SLACK_RESPONSE_URL_PREFIXES` (comma separated) says otherwise. Links in
replies use `$FORGE_PUBLIC_URL` if set, else the URL the hook arrived on.

API base allowlist: a trigger's `api_base` must be on `https://api.github.com` (GitHub) or `https://slack.com`
(Slack), default port, no credentials, query or fragment. The operator may allow more origins with
`$FORGE_TRIGGER_API_HOSTS` (comma separated; `ghe.example.com` means `https://ghe.example.com` on any port,
`http://127.0.0.1:9000` that exact origin). It is checked when a trigger is created or updated (400) and again
before every GitHub comment or Slack `chat.postMessage` (the delivery fails with a recorded error when the
stored base is no longer allowed).

Governance: trigger runs go through the same checks as any run. With the agent's monthly budget used up a fire
is recorded with status `error` and detail `402: Monthly budget ...` (the test endpoint answers 402) and no run
starts. Approval policies apply: an `ask` rule pauses the run (`awaiting_approval`, which a schedule with
`skip_if_running` treats as still running), and the GitHub comment or Slack reply is posted only after the
reviewer decides (or the approval times out) and the run finishes. Slack slash commands still get their
immediate `Working on it` reply.

### Scheduler

Started with the app (`FORGE_SCHEDULER=0` disables it; `FORGE_SCHEDULER_INTERVAL` seconds, default 15). Each slot fires
at most once: `last_slot_at` is advanced atomically before the run starts, so restarts never double fire. Slots more
than 10 minutes late are skipped and recorded as one `missed` fire; of several recent slots only the latest fires.
With `skip_if_running` a slot is recorded as `skipped` while the previous run is queued, running or awaiting approval. Enabling a schedule
or changing its cron or time zone starts counting from that moment. DST: a nonexistent local time runs shifted forward
by the gap; a repeated local time runs once, unless the hour field is `*`. Test hook: `FORGE_TEST_CLOCK_FILE` makes the
scheduler read the current unix time from that file.

## Promotion rule

Candidate eligible iff `candidate_passed_total > base_passed_total` and no case passed by base fails for candidate,
where "passed" is the per-case majority verdict above.
`auto_promote` agents: eligible candidates are promoted automatically; an improvement cycle also starts when 3 new
negative feedback items arrive since the last improvement.

## Observability

Built in and computed from Forge's own database (no third-party services). Aggregation lives in
`forge/metrics.py` (pure functions), prices in `forge/pricing.py`, routes in `forge/server/observability.py`.
Every change below is additive: existing fields, payloads and responses are unchanged.

### Additive event payload fields

Optional; events recorded before they existed simply lack them (consumers must treat them as optional).

| Event | New fields |
|---|---|
| `assistant` | `turn` (1 based), `duration_ms` (model call latency), `tokens_in`, `tokens_out` (this turn only) |
| `tool_call` | `turn`; the first call of a turn that produced no text also carries `model_duration_ms`, `tokens_in`, `tokens_out` (a tool-only turn emits no `assistant` event) |
| `tool_result` | `duration_ms` (tool execution time, excluding any approval wait); `approval_wait_ms` when the call waited on a human approval (governance) |
| `error` | `duration_ms` when the model call itself failed; for a refused turn (see "Stop reasons") also `turn`, `duration_ms`, `tokens_in`, `tokens_out` |

The per-turn usage on `assistant`, `tool_call` and refusal `error` events also carries `cache_read_tokens` and
`cache_write_tokens` when they are nonzero.

### Additive Run fields

`duration_ms` (end minus start, null until finished), `cost_usd` (the stored `runs.cost_usd`, see "Run cost"; null
when the model's price is unknown) and `cost_note` (`""`, `"self-hosted, not metered"` or `"unknown price"`). List endpoints also include `agent_slug`.

### Pricing

USD per million tokens (input / output): `claude-opus-5-5` 4 / 20, `claude-sonnet-5-5` 2 / 10, `claude-haiku-5-5`
0.10 / 0.50, `claude-opus-5` 5 / 25, `claude-sonnet-5` 2 / 10, `claude-opus-4-8` 5 / 25, `claude-sonnet-4-6` 3 / 15,
`claude-haiku-4-5` 1 / 5. `openai_compat:` and `fake:` models cost 0 and are labeled self-hosted (GPU time is billed
by the GPU provider). Unknown `anthropic:` models have a null cost. `FORGE_PRICES` (JSON) overrides or extends the
table: keys are full model strings or bare Anthropic ids, values `[input, output]` or `{"input": x, "output": y}`.
Run cost uses `runs.tokens_in/out` and the run's version model and is stored (see "Run cost" under Governance). Prompt-cache tokens (`cache_read_tokens`,
`cache_write_tokens`) are not priced yet: `tokens_in` is the uncached input count, and cache counts are reported
separately (Run objects, timelines, Health, per-version cost rows) until cache multipliers are documented here.

### Metric definitions

- Production metrics exclude `source = "eval"` runs unless `include_eval=true`.
- Success rate = succeeded / finished (succeeded, failed, timeout, budget_exceeded). `in_progress` counts queued,
  running and awaiting_approval runs; Health also reports `budget_exceeded` and `awaiting_approval` counts. Duration = `ended_at - started_at`; p50 and p95
  use linear interpolation. Thumbs up rate = +1 feedback items / all rated items.
- Periods: `days` back from now; "previous" is the equally long period before it. Buckets are hourly for 2 days
  or less, daily otherwise, aligned to the client's local midnight via `tz_offset_min` (minutes east of UTC).
- Failure categories: `timeout`, `budget_exceeded` (per-run spending cap, label "Spending cap reached"), `model_error`, `sandbox_error` (executor, runtime or interrupted), `tool_errors`
  (runs with `is_error` tool results; `count` is the number of failed calls), `eval_failed` (first line of the
  checker reason; always included because it is its own category). `eval_failed` uses each repeated run's own
  verdict (`eval_run_results`), since the breakdown counts runs: a run whose check failed counts even when its case
  passed by majority. Eval pass rates per version use the majority verdicts (`eval_results`), `negative_feedback` (the comment).
  Entries count runs (`runs`) and occurrences (`count`) and carry up to 3 `example_run_ids`, newest first.

### Endpoints

All `/v1` routes below are builder-only: localhost (or the agent's API key for agent-scoped routes, or
`FORGE_TRUST_ALL=1`), never share tokens.

| Method | Path | Returns |
|---|---|---|
| GET | `/metrics/overview?days=14&include_eval=false&tz_offset_min=0` | `{days, since, until, bucket, summary: Health, previous: Health, kpis: {runs, success_rate, p95_ms, cost_usd, thumbs_up_rate: Delta}, series: [Bucket], agents: [{slug, name, prod_version, health, failing_runs, flagged_runs, last_run_at, spark: [runs per bucket]}], top_failure_reasons: [Failure + agent, agent_name], top_failing_agents}` (localhost only) |
| GET | `/agents/{slug}/metrics?days=14&include_eval=false&tz_offset_min=0` | `{summary, previous, kpis, series, promotions, versions, cost, failures, tools, slowest_runs, prod_version, previous_prod_version, bucket, ...}` (below) |
| GET | `/runs/{id}/timeline` | `{run_id, status, estimated, total_ms, model_ms, tool_ms, approval_ms, other_ms, queue_ms, turns, tool_calls, spans: [Span], tokens_in, tokens_out, cache_read_tokens, cache_write_tokens, cost_usd, cost_note, price}` |
| GET | `/agents/{slug}/runs` | Extended with filters (below); still `[Run]`; total match count in the `X-Total-Count` header. Share-token callers only ever get share runs. |
| GET | `/runs` | Same filters plus `agent=<slug>`, across agents (localhost only) |

Run filters (all optional): `status`, `version`, `source` (comma separated lists), `rating` (`up` = has +1,
`down` = has -1, `none` = unrated), `has_tool_errors` (bool), `q` (case-insensitive substring of input or output),
`since`, `until` (unix seconds on `created_at`), `sort` (`created_at` | `duration` | `cost`), `order` (`desc` | `asc`),
`limit` (1 to 500, default 50), `offset`.

```jsonc
// Health
{"runs": 5, "finished": 5, "succeeded": 3, "failed": 1, "timeout": 1, "in_progress": 0, "success_rate": 0.6,
 "p50_ms": 410.0, "p95_ms": 2800.0, "avg_turns": 8.6, "feedback_count": 2, "thumbs_up": 1, "thumbs_down": 1,
 "thumbs_up_rate": 0.5, "tokens_in": 390, "tokens_out": 195, "cache_read_tokens": 0, "cache_write_tokens": 0,
 "cost_usd": 0.18, "cost_per_run": 0.036, "unpriced_runs": 0}
// Delta
{"value": 0.6, "previous": 0.8, "change": -0.2, "change_pct": -0.25}
// Bucket
{"t": 1791590400, "runs": 3, "succeeded": 2, "failed": 1, "timeout": 0, "in_progress": 0, "cost_usd": 0.12, "success_rate": 0.667}
// Agent metrics extras
"promotions": [{"version": 2, "from_version": 1, "source": "human", "at": 0.0}],   // create | human | auto
"versions": [{"version": 2, "model": "..", "created_by": "human", "created_at": 0.0, "is_prod": true,
              "is_previous_prod": false, "promoted_at": 0.0, "price": Price, "period_runs": 3, "health": Health,
              "eval": {"train": {"passed": 3, "total": 3, "evaluated": 3, "pass_rate": 1.0}, "holdout": {...}} | null,
              "delta_vs_previous_prod": {"success_rate": Delta, "p50_ms": Delta, "p95_ms": Delta, "avg_turns": Delta,
                                         "thumbs_up_rate": Delta, "cost_per_run": Delta,
                                         "train_pass_rate": Delta, "holdout_pass_rate": Delta}}],  // prod row only
"cost": {"total_usd": 0.18, "unpriced_runs": 0,
         "versions": [{"version": 1, "model": "..", "price": Price, "runs": 2, "tokens_in": 60, "tokens_out": 30,
                       "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.12, "cost_per_run": 0.06}],
         "improvements": [{"id": "im_..", "status": "eligible", "base_version": 1, "candidate_version": 2,
                           "eval_runs": 4, "eval_cost_usd": 0.12, "judge_cost_usd": 0.02, "optimizer_cost_usd": 0.04,
                           "grader_calls": 3, "tokens_in": 0, "tokens_out": 0, "total_cost_usd": 0.18,
                           "cost_complete": true, "created_at": 0.0, "ended_at": 0.0}]},
"failures": {"all": {"runs": 4, "categories": [Failure], "reasons": [Failure]}, "versions": [{"version": 3, ...}]},
"tools": {"tools": [{"name": "bash", "calls": 33, "errors": 1, "error_rate": 0.03, "p50_ms": 80.0, "p95_ms": 160.0,
                     "total_ms": 3100.0}],
          "slowest": [{"run_id": "r_..", "seq": 4, "name": "bash", "duration_ms": 181.0, "is_error": true,
                       "summary": "python hello.py", "version_n": 2}],
          "versions": [{"version": 2, "tools": [...]}]},
"slowest_runs": [{"id": "r_..", "version": 4, "status": "timeout", "source": "api", "duration_ms": 2800.0,
                  "input": "..", "cost_usd": 0.0, "created_at": 0.0}]
// Failure
{"category": "timeout", "label": "Timeout", "reason": "turn limit of 30 reached", "runs": 1, "count": 1,
 "example_run_ids": ["r_.."]}
// Price
{"model": "anthropic:claude-sonnet-5-5", "kind": "metered", "input_per_mtok": 2.0, "output_per_mtok": 10.0, "note": ""}
// Span (timeline)
{"kind": "model", "label": "Model turn 1", "turn": 1, "seq": 1, "start_ms": 0.0, "duration_ms": 512.3,
 "tokens_in": 10, "tokens_out": 5, "is_error": false, "estimated": false}
{"kind": "tool", "label": "bash", "turn": 1, "seq": 2, "start_ms": 512.3, "duration_ms": 40.1, "is_error": false,
 "estimated": false, "summary": "python hello.py"}
```

A tool_result with `approval_wait_ms` adds a span `{"kind": "approval", "label": "Approval for <tool>", ...}` right
before its tool span; that time is summed in `approval_ms` and excluded from `tool_ms` and `other_ms`.
Timelines for runs recorded before the timing fields existed are built from event timestamps (`estimated: true`):
a model turn spans from the previous step to the event that reports it, a tool call from `tool_call` to `tool_result`.
Improvement cycle cost = its eval runs (all k repeated runs of each case, linked when the cycle starts them;
stored runs reused from earlier evals are not re-counted) plus its judge and optimizer calls (one judge call per
judged run).

### Ops (no `/v1` prefix)

| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/healthz` | none | `{"status": "ok" or "error", "executor", "components": {"database": {...}, "docker": {...}}, "time"}`; 200 when every component is ok or skipped, else 503. Docker (daemon and `FORGE_SANDBOX_IMAGE`) is checked only when `FORGE_EXECUTOR=docker` (the default), cached 10 s. |
| GET | `/metrics` | localhost unless `FORGE_METRICS_PUBLIC=1` | Prometheus text: `forge_runs_total{agent,status,source}`, `forge_run_duration_seconds` histogram `{agent}`, `forge_tokens_total{agent,direction}`, `forge_cost_usd_total{agent}`, `forge_tool_calls_total{tool,error}`, `forge_active_runs` (queued, running and awaiting_approval), `forge_runs_awaiting_approval`, `forge_improvements_total{status}`. `forge_runs_total` status labels include `awaiting_approval` and `budget_exceeded`; `forge_cost_usd_total` sums the stored run costs. |

Structured logs: logger `forge.lifecycle` writes one JSON line per run event (`run_queued`, `run_started`,
`run_finished` with status, duration_ms, tokens and cost_usd) carrying `run_id` and `agent`. On when
`FORGE_LOG_JSON=1`, off when `0`; when unset, on unless stderr is a TTY.

### Additive schema

`promotions` (every prod pointer change: create, human, auto), `grader_calls` (judge and optimizer token usage,
with `improvement_id` and the judged `run_id`), `improvement_runs` (eval runs started by a cycle) and an index on
`runs(created_at)`. All are `CREATE ... IF NOT EXISTS`, so existing databases upgrade in place (columns added after the first schema,
such as `runs.cache_read_tokens`, go through `forge.db.ADDED_COLUMNS`, applied by `_migrate` on connect). Promotions before
this change are not known; an agent without records shows one synthetic promotion at its creation time.

## Governance: approvals and spending caps (additive, feat/governance)

All changes are additive; an agent without a policy, cap or budget behaves exactly as before.
Logic lives in `forge/policy.py` (rule matching), `forge/budget.py` (month-to-date spend and the budget check),
`forge/governance.py` (the `RunGovernor` hooks and the approval Future registry) and
`forge/server/governance.py` (routes).

### New fields

| Object | Field | Notes |
|---|---|---|
| Version | `approval_policy` | `{"rules": [Rule], "timeout_seconds": 900}`; `{}` means no rules. Copied from prod into new versions and optimizer candidates. Invalid policies are a 400. |
| Version | `max_run_cost_usd` | float or null (no cap). Explicit `null` in `POST /versions` removes the cap. |
| Agent | `monthly_budget_usd` | float or null (no budget). Set on `POST /agents` or `PATCH /agents/{slug}` (explicit `null` removes it). |
| Agent | `month_to_date_usd`, `budget_state`, `pending_approvals` | read only; `budget_state` is `none`, `ok`, `warning` (at 80%) or `exceeded` |
| Run | `cost_usd` | stored spend, updated after every model turn (see "Run cost") |

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
every hosted run, eval runs included, with one difference: eval runs (source `eval`) never pause, so an `ask`
rule there is an immediate deny with the tool result `Denied by policy during evaluation: <rule>` (no approval
row or events). The tool_result of a call that waited carries `approval_wait_ms`; its `duration_ms` is the tool's
own execution time only.

### New endpoints

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| GET | `/runs/{id}/approvals` | runtime (share allowed) | | `[Approval]` |
| POST | `/runs/{id}/approvals/{approval_id}` | localhost builder or API key; share token gets 403 | `{decision: "approve" or "deny", note?}` | `Approval`; 404 unknown, 409 no longer pending |
| GET | `/agents/{slug}/governance` | builder | | `{monthly_budget_usd, month_to_date_usd, fraction, state, pending_approvals, awaiting_runs: [run_id]}` |

`Approval`: `{id: "ap_..", run_id, tool_call_id, name, args, rule, status: "pending"|"approved"|"denied"|"expired",
note, resolved_by, created_at, expires_at, resolved_at}`.

Spending: month-to-date spend is the sum of `cost_usd` of the agent's runs created since the first of the
calendar month (UTC), eval runs included, plus the agent's judge and optimizer calls (`grader_calls`). When it is at or over `monthly_budget_usd`, `POST /agents/{slug}/runs`,
`POST /agents/{slug}/evals/run` and `POST /agents/{slug}/improve` return **402** `{"detail": "Monthly budget of $X
reached ..."}`; an improvement already running stops starting eval runs and ends `failed` with that reason.
Prices come from `forge.pricing` only (see Observability "Pricing", including `FORGE_PRICES`). Unknown models
are not priced: their `cost_usd` is null and caps cannot apply.

### Run cost

`runs.cost_usd` is the single source of truth for what a run cost. It is `forge.pricing.cost(model, tokens_in,
tokens_out)` over the run's cumulative uncached usage: 0 (or null for an unknown price) at creation, rewritten by
the `RunGovernor` after every model turn, and once more when the run is finalized. Run views, timelines, agent
metrics, the overview, improvement cycle cost, Prometheus `forge_cost_usd_total`, lifecycle logs, per-run caps and
monthly budgets all read this column, so they always agree. Prompt-cache tokens are counted, not priced. Databases
from before the column existed are backfilled on startup with the same function.

Webhook: when a run with a `callback_url` pauses, the URL also receives the Run JSON (status
`awaiting_approval`) with an extra `approval` field. The final Run JSON is posted as before.

Runtime: `run_agent(..., hooks=None)` accepts an optional hooks object with `after_turn(result, send)` and
`before_tool(call, send)`; the run manager always passes `forge.governance.RunGovernor`.
Local CLI runs are not governed.
