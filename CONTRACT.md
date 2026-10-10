# Forge contract

Shared by every component. Python types live in `forge/types.py`, the database in `forge/schema.sql`.
Change any of these only with a note in this file and updates to every implementer. Sections marked
amendment or additive extend the objects and endpoints above; where both describe one object, both apply.

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
| `forge/drafting.py`, `forge/pruning.py`, `forge/provenance.py`, `forge/server/authoring.py` | improve | Agent drafts, lesson pruning, lesson provenance (see Authoring) |
| `forge/metrics.py`, `forge/pricing.py`, `forge/server/observability.py`, `forge/server/obslog.py` | server | Observability (see Observability) |
| `forge/desktop_entry.py`, `desktop/` | desktop | macOS app and its frozen server entry point |
| `gpu/` | runtime | vLLM on Modal for GPU served open models |
| `forge/cli.py` | cli | `forge` Typer app |
| `web/` | web | React + Vite + Tailwind single-page app |
| `forge/copilot/`, `forge/server/copilot_api.py`, `web/src/components/copilot/` | assistant | Forge assistant side panel (see Assistant) |
| `demo/` | demo | `acme-orders` seed repo, `repo-maintainer` agent definition, eval cases; `examples/` example agents (`load_examples.py`, `verify_examples.py`) |
| `tests/` | each owner | `tests/test_<area>.py` |

## Conventions

- IDs: prefixed random hex, `ag_`, `v_`, `r_`, `fb_`, `ec_`, `im_`, `ap_` (approvals), `tr_`/`tf_` (triggers and
  fires), `s_` (sessions) + 12 hex chars.
- Times: unix seconds as float.
- Agent API keys: `fk_` + random URL-safe characters, stored hashed and shown once.
- Model strings: `anthropic:<model-id>` or `openai_compat:<base_url>|<model>`, e.g. `openai_compat:http://localhost:11434/v1|qwen3:4b-instruct`
  (after `ollama pull qwen3:4b-instruct`); `fake:<scenario>` selects a deterministic test model (TESTING.md).
- Data directory: `$FORGE_DATA_DIR` (default `./data`): `forge.db`, `runs/<run_id>/workspace.tar.gz`, `runs/<run_id>/diff.patch`.
- Secrets are redacted in event payloads by replacing each secret value with `[REDACTED]` before `emit`.
- Default models: the `default_model` setting (`$FORGE_DEFAULT_MODEL`, default `anthropic:claude-sonnet-5-5`) for
  agents, `grader_model` (`$FORGE_GRADER_MODEL`, falls back to the agent default) for judge, optimizer and agent
  drafting; see "Settings" for precedence. Judge and optimizer request temperature 0, which is sent only to models
  that accept sampling parameters (`forge.providers.accepts_temperature`; current Claude models do not).
- `http_tools` (`forge.types.HttpTool`), `mcp_servers` and `callable_agents` are offered to the model as tools
  (see Tools).

## HTTP API (prefix `/v1`)

Auth: `Authorization: Bearer <api_key>` (one key per agent) or `?share=<share_token>` (also header `X-Share-Token`);
requests from localhost without either act as the local builder. The full rules are under "Auth" below.
Errors: `{"detail": "<message>"}` with a 4xx/5xx status.

### Objects

```jsonc
// Agent
{"id": "ag_..", "slug": "repo-maintainer", "name": "Repo maintainer", "description": "",
 "prod_version": 1, "auto_promote": false, "eval_repeats": 3, "share_token": "..", "created_at": 0.0,
 "suggested_prompts": [],               // see "Suggested prompts"
 "versions": [Version, ...]}            // versions only on GET /agents/{slug}

// Version  (AgentVersion fields, JSON)
{"id": "v_..", "agent_id": "ag_..", "n": 1, "system_prompt": "..", "lessons": ["..."],
 "model": "anthropic:claude-sonnet-5-5", "tools": ["bash","read_file","write_file","edit_file"],
 "http_tools": [HttpTool, ...], "mcp_servers": [McpServer, ...], "callable_agents": ["other-agent"],
 "network": false, "seed_files": {"path": "content"},
 "parent_id": null, "created_by": "human", "rationale": "", "created_at": 0.0}

// Run
{"id": "r_..", "agent_id": "ag_..", "version_id": "v_..", "version_n": 1, "source": "api",
   // api|ui|share|eval|local|playground|agent|schedule|github|slack
 "input": "..", "status": "succeeded", "output": "..", "error": null,
   // status: queued|running|awaiting_approval (not terminal)|succeeded|failed|timeout|budget_exceeded|cancelled
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

### Auth

- Localhost clients act as the builder and may call every route without a key. A request carrying a proxy header
  (`CF-Connecting-IP`, `CF-Ray`, `X-Forwarded-For`, `X-Real-IP`, `Forwarded`) is remote even from 127.0.0.1, so a
  tunnel never gets the builder bypass. `FORGE_TRUST_ALL=1` disables every check.
- `GET/POST /agents` and `POST /agent-drafts` are localhost only; `/templates` is open; every agent-scoped builder,
  runtime, eval and improve route also accepts that agent's API key.
- A share token may `GET /agents/{slug}` (only `id, slug, name, description, suggested_prompts`), start runs
  (`source: "share"`), read a share run of that agent by id (run, events, artifacts, diff, files, approvals) and
  post feedback on it; any other run id is a 404 (see below). With a share token `GET /agents/{slug}/runs` lists
  only share-sourced runs. It gets 401 on versions, local runs, builder, eval, improve, observability, Playground
  and webhook-secret routes. Amendment ("Share and API conversations"): a share token may also hold
  conversations through the session endpoints and stop its own turns, all scoped to a visitor id; a share run
  that a visitor started is readable only with that visitor id.
- Share page URL: `/s/{slug}?share=<token>`.
- Share tokens reach only share runs: every run-scoped endpoint (`/runs/{id}`, its events, events.json,
  artifacts, diff, files, feedback and approvals) answers **404** (not 401, so ids are not confirmed) when a share
  token asks for a run whose `source` is not `share`, or for another visitor's share run. Timeline and
  save-as-eval are builder only; cancel and sessions follow "Share and API conversations".
- Webhooks (`callback_url`, run completion and governance's `awaiting_approval` notification): the URL must be
  `http` or `https` and its host must resolve only to public addresses, else **400** at request time. Deliveries
  go through the HTTP tools' SSRF-safe client (forge/tools/http.py: every resolved address checked, the
  connection pinned to the checked IP, every redirect hop re-checked, at most 5); `FORGE_ALLOW_PRIVATE_HTTP=1`
  disables the address check for local testing. Each delivery is `POST` with the Run JSON as body and headers
  `X-Forge-Event` (`run.finished` or `run.awaiting_approval`), `X-Forge-Delivery` (one id per delivery, kept
  across its retries) and `X-Forge-Signature-256: sha256=<hex HMAC-SHA256 of the raw body>` keyed with the
  agent's webhook secret. Builders read it with `GET /agents/{slug}/webhook-secret` (`{secret, header}`) and
  replace it with `POST /agents/{slug}/webhook-secret/rotate`; it is never part of the Agent object.
- Extras: Run includes `eval_case_id`; eval results include `"running": bool`; `PUT secrets` with an empty value deletes it.
- Narration (additive): `ModelResponse.notes: list[str]` carries the model's between-tool-call progress updates
  (Claude: `thinking.display: "updates"` summaries). The loop emits each note, then any text, as `assistant` events
  before that turn's `tool_call` events; `assistant` payloads gain `"kind": "progress" | "final"` (absent in old traces).

### Improvement

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/agents/{slug}/evals` | | `[EvalCase]` |
| POST | `/agents/{slug}/evals` | `{name, input, seed_files?, checker_type, checker, split}` | `EvalCase` |
| DELETE | `/evals/{id}` | | `{"ok": true}` |
| POST | `/runs/{id}/save-as-eval` | `{name?, checker_type?, checker?, split?}` (default judge rubric from feedback comment) | `EvalCase` |
| POST | `/agents/{slug}/evals/run` | `{version?: n}` | Same shape as GET results below (`running: true`); starts a cached eval of one version in the background; poll via GET |
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

## Workspace files (amendment)

Read-only browsing of a finished run's exported workspace (`runs/<run_id>/workspace.tar.gz`), implemented in
`forge/server/files.py`. Auth is the same as the other run endpoints (localhost builder, the agent's API key, or
its share token); a share token may only browse runs whose `source` is `share` (401 otherwise).

| Method | Path | Returns |
|---|---|---|
| GET | `/runs/{id}/files?changed=0` | `[{path, size, is_text}]` sorted by path; with `changed=1` each entry also has `changed: bool` and, when true, `change`: `added`, `modified` or `renamed` (from the stored `diff.patch`) |
| GET | `/runs/{id}/files/{path}` | `text/plain; charset=utf-8` file content |

- Paths are archive relative POSIX paths without a leading `./`. `.git` and anything under it is never listed or
  served. Only regular files are listed; a symlink or hard link is listed (with its target's size and type) only when
  it resolves, within 8 hops, to a regular file inside the archive. Links pointing outside the archive, absolute
  links, loops and dangling links are omitted and read as 404.
- `is_text`: the first 8 KB contain no NUL byte and decode as UTF-8. Listings are capped at 10,000 files.
- Reading: 400 for a path that is empty, absolute, contains `..` or names `.git`; 404 when the run has no archive
  or the file is missing; 415 for a binary file; 413 for a text file over 1 MB. Responses carry
  `X-Content-Type-Options: nosniff`. Nothing is extracted to disk; archives are indexed in memory and cached by
  path, mtime and size.
- Deleted files are not in the archive, so they never appear in the listing; see `/runs/{id}/diff`.

### `forge chat` (client of the sessions API)

`forge chat <slug>[@vN] [--session <id>] [--url <server>]` opens a REPL: it creates a session
(`POST /agents/{slug}/sessions`, `{version}` when pinned) or resumes one (`GET /sessions/{id}`), sends each line with
`POST /sessions/{id}/messages`, and renders the turn's `GET /runs/{id}/events` stream (reconnecting with `?after=<seq>`).
Ctrl+C during a turn calls `POST /runs/{id}/cancel`; a second Ctrl+C at the prompt, Ctrl+D or `/exit` leaves.
`/new`, `/files` and `/open <path>` (the latest turn's workspace, via the endpoints above) and `/help` are built in.
The transcript renderer lives in `forge/cli_render.py` (plain ANSI; honors `NO_COLOR`; the live status line is drawn
only on terminals).

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

### Sessions

Playground sessions (additive). Localhost or the agent's API key; share tokens get visitor scoped conversations
(see "Share and API conversations"). JSON.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/agents/{slug}/sessions` | `{version?: int, draft?: {system_prompt?, model?, tools?, network?, lessons?}}` | `Session` |
| GET | `/agents/{slug}/sessions?limit=20` | | `[Session]` newest first (turns omitted) |
| GET | `/sessions/{id}` | | `Session` with `turns: [Run]` ordered by `turn_index` |
| POST | `/sessions/{id}/messages` | `{input: str}` | `Run` (status `queued`/`running`; has `session_id`, `turn_index`); 409 if a turn is still running |
| POST | `/runs/{id}/cancel` | | `Run` (status becomes `cancelled`; idempotent; 409 if already finished) |

```jsonc
// Session
{"id": "s_..", "agent_slug": "repo-maintainer", "version_n": 1,   // int, or null for a draft
 "draft": null,                                                    // draft config object, or null
 "title": "..", "created_at": 0.0, "updated_at": 0.0}
```

- Run objects gain `session_id` (str|null) and `turn_index` (int|null). Session turns are runs with source `playground`.
- Each turn is a normal run: its model history is rebuilt from the earlier turns' events (user inputs, assistant
  text, tool calls and results; tool outputs clipped, oldest turns dropped to fit), and its workspace starts from
  the latest earlier turn's exported archive. Containers are not kept between turns.
- A draft session runs its unsaved config over the version it was started from (`version`, default prod) without
  creating a version. Draft runs are excluded from `GET /agents/{slug}/runs`, metrics and improvement feedback,
  and never affect evals or promotion.
- Implementation extras (not required by clients): Session also carries `agent_id`, `base_version_n`, `is_draft`,
  `turn_count` and (on `GET /sessions/{id}`) `running`; `POST /sessions/{id}/messages` accepts `wait: bool`;
  `POST /sessions/{id}/save-version {rationale?}` creates (does not promote) a version from a draft's config.
- Run status gains `cancelled` (additive). Cancel stops the task and the sandbox, appends an `error` event
  `"Stopped by user"`, then `run_finished` with status `cancelled`; it works for every server run (local CLI runs
  return 409). The cancelled turn's workspace is still exported, so a session continues from it.

## Assist (additive)

Generate buttons: grader-written suggestions for fields users would otherwise type by hand. Every kind goes through
one registry (`forge/assist/registry.py`), one engine (`forge/assist/engine.py`) and one route
(`forge/server/assist.py`). Built-in kinds: `task`, `eval_case`, `eval_cases`, `instructions`, `rubric`,
`seed_file`, `version_note`, `approval_rules`, `budget`, `http_tool`, `cron`, `trigger_template`.

### Endpoint

`POST /v1/assist/{kind}` body `{slug?, version?, input?: object, avoid?: [str]}` returns
`{result: object, rationale: str}`. `result` is kind-specific; `input` is kind-specific context (current field
value, a run id, a description...); `avoid` lists texts the suggestion must not repeat (the web button sends its
earlier results). Builders (localhost or the agent's API key) also get `kind`, `version` (or null) and
`diagnostics` (kind-specific: what the context contained). Errors: unknown kind `404`; agent or version not found
`404`; `slug` missing for a kind that needs an agent `400`; a kind without an agent from a non-local client `401`;
share token on a kind without `share_allowed` `401`; share budget spent `429` with `Retry-After`; model failure or
an invalid reply after the retry `502` with the reason.

Alias: `POST /v1/agents/{slug}/sample-task` body `{version?, hint?, avoid?}` is kind `task` with a flat response
`{title, prompt, rationale}` (builders also get `version`, `variety` and `context`).

### Rules (every kind)

- Never saves anything: the result only fills a field; the user decides what to keep.
- Never generates or reveals secrets: kinds see configured secret NAMES only, never values, and any secret value
  that still appears in a result is replaced with `[REDACTED]`. No kind writes feedback or ratings.
- Grounded in the agent: kinds with an agent build on the shared context (`forge/assist/context.py`
  `agent_context`): the version's system prompt (over 6000 chars condensed to its opening and end), name,
  description, tools, network setting, configured secret names, up to 200 seed file paths plus excerpts of the most
  informative files (README, top-level modules, configs, CSV headers with row counts, first lines of logs; at most
  12000 chars), the agent's `suggested_prompts` when present, and the last 10 distinct run inputs.
- One grader call (`FORGE_GRADER_MODEL`) per request with structured output: Claude gets `output_config.format`
  with the envelope schema `{result: <kind schema>, rationale}` and no temperature; other providers get the same
  prompt and are parsed leniently. An invalid reply (bad shape, missing result keys, or the kind's validator)
  is retried once as a fresh request listing the problems.
- Metered: calls with an agent are recorded in `grader_calls` with kind `assist:<kind>` (no improvement), and agent
  metrics `cost` gains `assist: {calls, kinds: {<kind>: n}, tokens_in, tokens_out, cost_usd, cost_complete}` for
  the period. Calls without an agent (localhost only) are not metered, like drafts.
- Share links: only kinds registered with `share_allowed`, always on prod (`version` ignored), only
  `{result, rationale}` in the response, and at most 10 calls per hour per share token across all kinds, in memory
  (`FORGE_ASSIST_SHARE_LIMIT` overrides; restarting the server resets it).

### Kind `task`

`input`: `{hint?: str, current?: str}` (`current`, the text already in the box, is avoided like `avoid`). The
server also remembers the agent's last 10 task results in memory (registry `result_text`) and shows them as
earlier suggestions, so plain API calls differ too; unlike `avoid` they guide the model but are not validated.
`result`: `{title, prompt}`. The prompt asks for ONE concrete, self-contained task a real user would send,
solvable in the sandbox with the given files and tools (offline when network is off, no unconfigured
credentials), naming real files, endpoints or columns, 1 to 4 sentences phrased as a teammate's request, with a
`variety` (type and difficulty) cycled by the number of earlier `assist:task` calls for the agent. Valid when the
prompt is non-empty and under 1200 chars, the title is under 100 chars, and the prompt is not a near duplicate
(normalized text similarity at least 0.85) of a recent input or an avoided text. Share links allowed.

### Kind `eval_case`

One evaluation case (task input plus checker) drafted for the agent. Never saved: the client creates it with
`POST /agents/{slug}/evals` (or `POST /runs/{id}/save-as-eval`) only when the user clicks Add. Builders only (no
share links). Stable interface (other features call it):

`input` (all optional): `{split?: "train"|"holdout" (default train), focus?: str, checker_type?: "shell"|"judge"
(default: the model picks shell when the result is checkable by commands, else judge), from_run_id?: str,
task?: str}`. `from_run_id` (one of this agent's runs, else `400`) turns the run's input, reply and feedback into a
case whose input is the run's input verbatim; `task` fixes the case input (verbatim) so only the checker and name
are written (the Add case form's checker button).
`result`: `{name, input, checker_type: "shell"|"judge", checker, split: "train"|"holdout", rationale}`. The
context is the shared agent context plus the existing cases (split, type, name, input; the agent's own longest
shell checker as a style reference), `avoid`, and the request. Valid when: `name` is a lowercase hyphenated slug
under 60 chars not used by an existing case; `input` is non-empty, under 3000 chars, and not a near duplicate of
an existing case input or an avoided text (or equals the fixed input); `checker_type` and `split` match the request
when given; a shell checker contains `set -euo pipefail` and a `FAIL` line and, when network is off, no network
commands (curl, wget, pip install, npm install, apt install, git clone, uv add); a judge rubric says "Pass only if"
and lists at least two numbered criteria; `rationale` is non-empty. Holdout requests ask for a different task that
exercises the same skills as the train cases.

### Kind `eval_cases`

A batch for the "Generate several" review list. `input`: `{n?: 1..6 (default 3), holdout?: 0..n (default n // 2),
focus?: str, checker_type?: "shell"|"judge"}` (out of range `400`). `result`: `{cases: [<eval_case result>]}` with
exactly `n` cases, exactly `holdout` of them holdout, each valid as above, with unique names and no near duplicates
among themselves.

### Configuration and deploy kinds

All builders only (no share links), all grounded in the shared agent context unless noted, all fill fields only.
A missing required input or an out of range option is `400`. Some kinds convert the model's fields or add
computed ones after validation (registry `finalize`); the result below is what the endpoint returns.

| Kind | `input` | `result` | Valid when |
|---|---|---|---|
| `instructions` | `{current?: str (the unsaved text; default the version's prompt), goal?: str}` | `{instructions, summary}` | The context adds the full current text, learned lessons (not to be copied in), the last 10 negative feedback comments and the failing eval cases of the version. Instructions non-empty, under 20000 chars, changed, between 0.4 and 2.5 times the current length when that is at least 400 chars, never naming an eval case; summary non-empty, under 300 chars. The web shows a diff to accept or reject. |
| `rubric` | `{task: str (required), current?: str}` | `{rubric}` | Says "Pass only if", at least two numbered criteria, a line starting "Fail if", differs from `current`. |
| `seed_file` | `{description?: str, path?: str, existing?: [str] (unsaved paths)}` | `{path, content, description}` | Relative path without `..`, the requested path when given, otherwise not an existing path; content non-empty, under 20000 chars; CSV with a header, two rows and a constant column count; JSON and Python parse; nothing that looks like a real credential. |
| `version_note` | `{config: {system_prompt?, model?, tools?, network?, seed_files?, http_tools?, mcp_servers?, callable_agents?, approval_policy?, max_run_cost_usd?}}` (missing keys are unchanged) | `{note}` | The server diffs the draft against the request's version (prod by default); no change is `400`. One paragraph under 300 chars, no dashes as punctuation. |
| `approval_rules` | `{tools?: [str], network?: bool, http_tools?: [str], current?: {rules}}` (defaults from the version) | `{rules: [{tool, pattern, mode: "ask"\|"deny", label}], explanation}` | 1 to 8 rules, each a valid policy rule (`forge/policy.py`) naming a tool the agent has (or `*`, `mcp__<server>__...`, `agent__<slug>`), with a label, not already in `current`, no repeats; `pattern` `""` means every call. |
| `budget` | `{}` | `{max_run_cost_usd, monthly_budget_usd, explanation, stats: {runs, mean_usd, p95_usd, max_usd, monthly_usd, grader_usd, days}}` | `stats` come from the last 30 days of priced, finished, non draft runs plus grader spend (`monthly_usd` is the projection). No priced run yet is `400`. Cap positive and at least the 95th percentile; monthly at least the cap and the projection. |
| `http_tool` | `{source: str (required; description, curl command or API doc), existing?: [str]}` | `{name, description, method, url, parameters: object, headers: {name: value}, query_params: [str]}` | Passes the version tool validation (name, JSON schema object, https URL, placeholders are parameters); name not in use; credential headers are `{{SECRET}}` references; no credentials in the URL. Literal credentials found in the source are replaced with `[REDACTED]` before the model sees it and may not appear in the result. |
| `cron` | `{text: str (required), timezone?: str (default zone)}` | `{cron, timezone, explanation, next_fire_times: [unix seconds] x3}` | Parses with `forge/triggers/cron.py` and the zone exists; `next_fire_times` computed by the server. Needs no agent (localhost only without `slug`, metered with one). |
| `trigger_template` | `{source: "github"\|"slack" (required), event?: issues\|issue_comment\|pull_request\|push (GitHub), slash\|mention (Slack), current?: str}` | `{template, explanation}` | Uses at least one `{field}` placeholder and only the source's template fields (see Triggers), under 4000 chars, differs from `current`. |

### Adding a kind

1. Create `forge/assist/kinds/<kind>.py` with a system prompt (it must differ from every other kind's), a result
   schema (object, `additionalProperties: false`, every property required, no length or range constraints),
   `build_context(req: AssistRequest) -> Prepared(text, data, diagnostics)` (use `context.agent_context` and
   `render_agent_context` for agent grounding, `req.input` for the kind's own fields), `validate(result, data)
   -> [problems]`, and a deterministic `fake(user_text) -> {result, rationale}` for `fake:grader`. Finish with
   `SPEC = register("<kind>", build_context, SYSTEM, SCHEMA, validate, share_allowed=..., needs_agent=..., fake=fake)`.
   Optional: `finalize(result, data) -> result` converts the model's reply to the documented result after
   validation (a JSON string to an object, computed fields), and `result_text` feeds the server's earlier
   suggestions. A `ValueError` from `build_context` is a `400`.
2. Import the module in `forge/assist/kinds/__init__.py`.
3. Web: mount `<AssistButton kind="<kind>" slug=... input={...} resultText={...} onResult={...} undoValue={value}
   onUndo={setValue} inputRef={ref} ariaLabel="..." />` next to the field (or wrap it like `GenerateTaskButton`).
4. Tests: validator and context unit tests, the fake through `POST /v1/assist/<kind>`, and a Vitest for the mount.
   Document the kind's `input` and `result` here.

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
| GET | `/healthz` | none | `{"status": "ok" or "error", "executor", "components": {"database": {...}, "docker": {...}}, "build", "time"}` (`build` is the `/v1/version` object); 200 when every component is ok or skipped, else 503. Docker (daemon and `FORGE_SANDBOX_IMAGE`) is checked only when `FORGE_EXECUTOR=docker` (the default), cached 10 s. |
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

## Suggested prompts (additive)

Starter tasks a person can run immediately, shown as clickable chips above the run input (Runs tab, the
Playground and the share page). They belong to the agent, not to a version: they describe what to try, not how the agent behaves,
so changing them never creates a version and never affects evals.

```jsonc
// Agent gains
"suggested_prompts": [{"title": "Find the 5xx root cause", "prompt": "Investigate the logs in /workspace ..."}]
```

| Where | Behavior |
|---|---|
| `POST /agents` | optional `suggested_prompts` |
| `PATCH /agents/{slug}` | optional `suggested_prompts`; when present it replaces the list (`[]` clears it), when absent it is kept |
| `POST /agents/{slug}/versions` | optional `suggested_prompts`, same replace semantics as PATCH; the Version object does not carry it |
| `GET /agents`, `GET /agents/{slug}`, Agent responses | always include `suggested_prompts` (`[]` when none) |
| `GET /agents/{slug}` with a share token | returns exactly `id, slug, name, description, suggested_prompts`; the share page shows the chips above its input, since the prompts are written for users of the agent |

Validation: at most 12 items; `title` 1 to 80 characters, `prompt` 1 to 20000 characters; otherwise 422.
Storage: `agents.suggested_prompts` (JSON text, default `'[]'`), added to existing databases by the guarded
`ADDED_COLUMNS` migration in `forge/db.py`.

## Examples (additive)

The example agents shipped in `demo/examples` (log triage, data analyst, docs writer, repo maintainer) can be
installed from the API and the web app with one click. Builder only: every route requires a localhost caller
(the same rule as `POST /agents`); remote callers get 401. Logic lives in `forge/examples.py`, routes in
`forge/server/examples.py`. The data is read from `demo/` next to the package, or from `sys._MEIPASS/demo` in
the frozen desktop server (`FORGE_EXAMPLES_DIR` overrides). Only `agent.json`, `evals.json` and seed files are
read or bundled; reference solutions and `prompt_checks.json` never are.

```jsonc
// GET /examples -> ExampleInfo[] (sorted by key)
{"key": "log-triage", "name": "Log triage", "description": "...", "agent_slug": "log-triage",
 "installed": false, "eval_cases": 4, "suggested_prompts": 3, "needs_network": false}
```

| Route | Body | Result |
|---|---|---|
| `GET /examples` | | `ExampleInfo[]`; `installed` is true when an agent with `agent_slug` exists |
| `POST /examples/{key}/install` | `{model?}` | Agent with `versions`, plus `example`, `created`, `installed_version`, `eval_cases_synced {added, replaced, kept}`, and `api_key` (shown once) when created. Unknown key 404. Existing slug 409 with a clear message unless `?replace=1` |
| `POST /examples/{key}/install?replace=1[&promote=1]` | `{model?}` | Like `demo/load_examples.py`: a new version only when the system prompt, tools, network, seed files or model differ from prod (promoted only with `promote=1`), suggested prompts restored, eval cases synced by name (unchanged kept, changed replaced, missing added). No `api_key` |
| `POST /examples/install-all` | `{model?}` | `{"installed": [same as install], "skipped": [slug]}`; installed examples are skipped (with `?replace=1` they are re-synced instead) |

Model: `model` when given (any provider string, e.g. `openai_compat:http://localhost:11434/v1|qwen3:4b-instruct`),
else the example's own model, else (on replace) the current prod model, else the server default
(`FORGE_DEFAULT_MODEL`). The version created on install has `created_by: "human"` and the rationale
"Installed from the <key> example".

## Settings (additive)

Every Forge setting is declared once in `forge/settings.py` (the registry) and can be viewed and changed on the
web Settings page (`/settings`, gear icon) and in the desktop app, with no `.env` editing.

**Registry.** Each setting has `key`, `env` (environment variable), `type` (`string`, `int`, `float`, `bool`,
`enum`, `model`, `url`, `json`, `secret`), `default`, `group` (Models, Sandbox, Evaluations, Server,
Observability, Pricing, Sharing, or a new one), `label`, `description` (one plain sentence), validation (`min`,
`max`, `options`, `optional`, or a `validate` function), `apply` (`live`: read where it is used, so a change takes
effect at once; `restart`: read at server start) and `secret`. `editable=False` marks environment only settings.
Code reads a value with `settings.get(key)` at the point of use.

**Precedence.** A non empty environment variable (shown as "Environment", read only) > a value saved in the
`settings` table (`key TEXT PRIMARY KEY, value TEXT` (JSON)`, updated_at REAL`, created `IF NOT EXISTS` by
`forge/settings.py`) > the default. An empty variable counts as unset. An invalid environment value keeps the
setting read only, falls back to the default and is reported in `error`.

**Secrets** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, endpoint key variables) are never stored in the database
and never returned by the API, only whether they are set. The desktop app keeps them in the Keychain and passes
them as environment variables, naming them in `FORGE_DESKTOP_SECRETS`; the dev server reads them from `.env`.

**OpenAI compatible endpoints** (`openai_endpoints`): `[{"name", "base_url", "api_key_env"?}]`, default Ollama at
`http://localhost:11434/v1`. A provider for `openai_compat:<base_url>|<model>` sends the key from the matching
endpoint's `api_key_env` variable, else `OPENAI_API_KEY`.

| Method | Path | Auth | Body / returns |
|---|---|---|---|
| GET | `/settings` | localhost | `{desktop, needs_restart, groups: [{name, settings: [Setting]}], info: {data_dir, version, executor, docker}}` |
| PATCH | `/settings` | localhost | `{key: value, ...}` (`null` resets a key). All or nothing: 422 `{"detail": {"message", "errors": {key: message}}}` for unknown keys, secrets, environment owned or invalid values; else the GET payload |
| POST | `/settings/reset` | localhost | `{"keys": [...]}` forgets saved values; returns the GET payload |
| GET | `/models/available` | localhost | `?refresh=true` skips the cache, `?provider=<id or name>` checks one. `{"providers": [{id, name, kind, base_url, prefix, configured, reachable, models, error, checked_at}]}`. Claude: `GET /v1/models` with the configured key; endpoints: `GET {base_url}/models`. Cached 60 s per provider; a provider that is down is reported unreachable, never a failed request |

```jsonc
// Setting (secrets have is_set and from_desktop instead of value and default)
{"key": "eval_repeats", "env": "FORGE_EVAL_REPEATS", "type": "int", "label": "Default eval repeats",
 "description": "...", "group": "Evaluations", "apply": "live", "secret": false, "source": "saved",
 "editable": true, "needs_restart": false, "error": null, "value": 5, "default": 3, "min": 1, "max": 9}
```

`needs_restart` is true for a restart setting whose effective value differs from the value when the server
started. Restart: inside the desktop app the page calls `window.forgeDesktop.restartServer()`, an IPC call to the
Electron main process, which restarts the server it owns and reloads `/settings`; the server exposes no restart
endpoint. Outside the app the page asks to restart the server by hand.

**Model availability warnings.** `POST /agents` and `POST /agents/{slug}/versions` responses gain an optional
`warning` when the model is not available right now (saved anyway), for example
`"Model 'qwen3:8b' is not available on http://localhost:11434/v1; available: qwen3:4b-instruct"`, or a missing
Claude key. **Eval early stop:** a provider 404 for the model raises `forge.providers.ModelNotFoundError`; an eval
then skips the version's remaining runs and cases, records no verdicts, and `GET /agents/{slug}/evals/results`
reports `error` (`"Stopped evaluating v2 early: ..."`); an improvement cycle using that eval fails with it.

**Registering settings from other modules.** Call `register_setting(...)` at import time of the module that uses
it, read it with `settings.get(key)`, then run `uv run python scripts/gen_env_example.py` (a test fails when
`.env.example` drifts from the registry). The Settings page renders new settings and groups with no web changes.

```python
from forge.settings import register_setting
register_setting(key="share_rate_limit", env="FORGE_SHARE_RATE_LIMIT", type="int", default=30, min=1, max=1000,
                 group="Sharing", label="Share link rate limit", description="Runs per minute allowed per share link.")
```

Registered by other modules (listed in `settings.SETTING_MODULES`, which `settings.load_all()` imports before
the registry is listed): `trigger_api_hosts`, `trigger_rate_limit` and `slack_response_url_prefixes` (group
Triggers, `forge/triggers`), `assist_share_limit` (Sharing, `forge/server/assist.py`), `share_session_turns`, `share_sessions_per_visitor`,
`share_daily_turns`, `share_concurrent_turns` and `session_idle_hours` (Sharing, `forge/share_limits.py`) and `mcp_connect_timeout`
(Tools, `forge/tools/mcp.py`). Triggers read `public_url` for hook paths and reply links. Governance budgets are
per agent fields, not settings. Development and test switches (`FORGE_ALLOW_PRIVATE_HTTP`, `FORGE_SCHEDULER`,
`FORGE_TEST_CLOCK_FILE`, desktop entry, scripts) stay environment only and are listed at the end of `.env.example`.

## Build info (additive)

`GET /v1/version` (no auth, no secrets, also through the public tunnel) returns the running build. `/healthz`
includes the same object as `build`.

```json
{"version": "0.1.3", "commit": "473b4fa", "built_at": "2026-10-10T19:30:00Z", "channel": "release"}
```

| Field | Notes |
|---|---|
| `version` | `0.1.N` for releases, `0.1.N-preview` for previews, `<latest tag>+dev` (for example `0.1.2+dev`) on a dev server; null when unknown |
| `commit` | short commit hash, or null |
| `built_at` | ISO 8601 UTC build time; null on dev servers |
| `channel` | `release`, `preview` or `dev`; null when unknown |

`forge/buildinfo.py` takes each field from the first source that knows it: env vars `FORGE_VERSION`,
`FORGE_COMMIT`, `FORGE_BUILT_AT`, `FORGE_CHANNEL` (set by the desktop app); then `build-info.json` next to the
module (bundled into the frozen server by `desktop/scripts/build-server.sh`); then, outside a frozen build,
the repository's `git describe --tags --always --dirty` and `git rev-parse --short HEAD` (cached per process,
channel `dev`). A source with a version but no channel gets one from the version. It never fails: unknown
fields are null. The web header shows it as a badge (`v0.1.3 · 473b4fa`, `v0.1.4-preview · abc1234`,
`dev · abc1234`).
## Share and API conversations (additive)

Multi-turn conversations for people who use a deployed agent: share link visitors and API callers use the
session endpoints of "Sessions" above. Code: `forge/server/sessions_api.py` (routes), `forge/share_limits.py`
(visitors, limits, expiry), `forge/triggers/core.py` (Slack threads). Every session turn is an ordinary run with
the session's history and the previous turn's workspace, as in the Playground.

| Caller | Channel | Version | Turn `source` | Sees |
|---|---|---|---|---|
| Localhost builder | `playground` | any, or a draft | `playground` | every session (`?channel=` filters; default `playground`) |
| Agent API key | `api` | any (`{version}`), or a draft | `api` | the agent's sessions (default `?channel=api`; `all` for every channel) |
| Share token | `share` | the production version at each turn (no version or draft; either is a 400) | `share` | only its own visitor's sessions and runs |
| Slack `app_mention` | `slack` | the trigger's pinned version or production | `slack` | (no reader; replies go to the thread) |

Endpoints (all JSON, prefix `/v1`):

| Method | Path | Share token behavior |
|---|---|---|
| POST | `/agents/{slug}/sessions` | Body `{}` (or `{title}`). Issues a visitor id when `X-Forge-Visitor` is missing or unknown for this agent, returns it as `visitor_id` and in the `X-Forge-Visitor` response header. 429 `sessions_per_visitor` |
| GET | `/agents/{slug}/sessions?limit=20` | Only the visitor's conversations; `[]` without a visitor id |
| GET | `/sessions/{id}` | 404 unless the session belongs to this visitor |
| POST | `/sessions/{id}/messages` | `{input, wait?}`. 404 for another visitor's session, 410 once expired, 409 while a turn runs, 429 for a limit, 402 for the monthly budget |
| POST | `/runs/{id}/cancel` | Allowed for the visitor's own share runs; any other run is a 404 |
| GET | `/runs/{id}`, events, events.json, files, artifacts, diff, approvals; POST feedback | The visitor's own share runs. Share runs without a visitor (one shot runs) stay readable by id as before; every other run is a 404 |

Visitor scoping:
- A visitor id is `vis_` plus 32 random URL-safe characters, stored in `share_visitors` (per agent) and on
  `sessions.visitor_id` and `runs.visitor_id`. Clients send it as `X-Forge-Visitor`, or as `?visitor=` where
  headers cannot be set (EventSource streams, download links). The web share page keeps it in localStorage
  (`forge.share.visitor.<slug>`).
- A share token without a visitor id reaches no visitor's sessions or runs; `GET /agents/{slug}/runs` then lists
  only share runs that no visitor owns. With a visitor id it lists only that visitor's runs.
- Visitors never see the builder's or other visitors' sessions or runs, and session views for share callers
  leave out `draft`, `base_version_n`, `is_draft` and `agent_id`.
- `POST /agents/{slug}/runs` with a share token always runs production and records the visitor when the
  request carries a known one. It counts toward the daily and concurrency limits below.

Session objects gain (additive): `channel`, and for share and Slack sessions `expires_at` and `expired`; share
sessions also `max_turns`. Share and Slack conversations idle for `FORGE_SESSION_IDLE_HOURS` (default 24) are
read only: messages answer **410** (a Slack thread starts a new conversation instead). A running turn keeps a
session open. Workspace archives of expired sessions stay under `runs/<run_id>/` like every run's (there is no
automatic retention yet).

Limits per share link (429 `{"detail", "limit", "retry_after"}` plus a `Retry-After` header, in seconds):

| `limit` | Default | Env (global default) | Counted | Retry-After |
|---|---|---|---|---|
| `session_turns` | 20 | `FORGE_SHARE_SESSION_TURNS` | turns in one conversation (cancelled included) | 0: start a new conversation |
| `sessions_per_visitor` | 3 | `FORGE_SHARE_SESSIONS_PER_VISITOR` | the visitor's conversations that have not expired | until the oldest one expires |
| `daily_turns` | 200 | `FORGE_SHARE_DAILY_TURNS` | runs with source `share` created since 00:00 UTC (one shot share runs included) | until the next 00:00 UTC |
| `concurrent_turns` | 2 | `FORGE_SHARE_CONCURRENT_TURNS` | share runs `queued`, `running` or `awaiting_approval` | 15 |

- The daily message is exactly "You've reached today's limit for this link. Try again tomorrow."
- Per agent overrides: `PATCH /agents/{slug} {"share_limits": {"daily_turns": 50}}`. Keys present replace, `null`
  clears one (back to the default), absent keys are kept; each value is 1..100000 (422 otherwise). Agent objects
  carry `share_limits` (overrides, null when unset), `share_limits_effective`, `share_limit_defaults` and
  `session_idle_hours`. The Deploy tab edits them next to the share link.
- Checks run on the server before the run row exists, without an await between the check and the insert, so
  concurrent requests on the single server process cannot both pass.
- Governance applies to every share turn: the monthly budget (402; share callers get "This agent has reached its
  usage limit for this month. Try again later." without figures) and the version's per-run cap
  (`budget_exceeded`). Builder and API sessions have no share limits and do not expire.

Slack threads: an `app_mention` continues the conversation keyed by trigger id, channel and `thread_ts` (the
mention's own `ts` for a top-level mention), so replies that mention the bot in the same thread continue it with
history and files. The per conversation limit (`session_turns`) applies; a full thread, or a thread with a turn
still running, gets a short note posted in the thread (bot token required), the fire is recorded as `skipped`
and the hook answers 200 `{"ok": true, "skipped": "<reason>"}` so Slack does not retry. Slash commands carry no
thread, so each starts its own run as before. A Slack session expires after the same idle time; the next mention
in that thread starts a fresh conversation.

API example:

```bash
SESSION=$(curl -s $FORGE/v1/agents/my-agent/sessions -H "Authorization: Bearer $FORGE_API_KEY" \
  -H "Content-Type: application/json" -d '{"version": 2}' | jq -r .id)
curl -s $FORGE/v1/sessions/$SESSION/messages -H "Authorization: Bearer $FORGE_API_KEY" \
  -H "Content-Type: application/json" -d '{"input": "Create notes.md with three ideas", "wait": true}'
curl -s $FORGE/v1/sessions/$SESSION/messages -H "Authorization: Bearer $FORGE_API_KEY" \
  -H "Content-Type: application/json" -d '{"input": "Now expand the second idea", "wait": true}'
```
## Assistant (additive)

A chat side panel on every page of the web app (and so the desktop app) that explains Forge and operates it for
the user. Backend in `forge/copilot/` (prompt, tools, engine, docs search, fake model) and
`forge/server/copilot_api.py`; web in `web/src/components/copilot/` and `web/src/copilotApi.ts`.

### Endpoints (prefix `/v1/copilot`)

Builder only: localhost, or any agent's API key; share tokens and anonymous remote callers get 401. A session
belongs to its caller (`local`, or the key's agent) and other callers get 404. Tools act with the caller's own
credentials through Forge's HTTP API in process, so an API-key caller can only change its own agent.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/sessions` | `{context?}` | `CopilotSession` |
| GET | `/sessions?limit=10` | | `[CopilotSession]` newest first (limit 1 to 50) |
| GET | `/sessions/{id}` | | `CopilotSession` + `messages: [CopilotEvent]` + `pending_confirm` (the open `confirm_request` payload or null) |
| POST | `/sessions/{id}/messages` | `{text: 1..8000 chars, context: {page?, agent_slug?, run_id?, version?}}` | SSE stream of the turn's `CopilotEvent`s (`event: <type>`, `id: <seq>`, `data: CopilotEvent`), ending with `event: end`; 409 while a turn is running |
| GET | `/sessions/{id}/events?after=<seq>` | | SSE: re-attaches to the latest turn after `seq` (reload while running); ends at once when idle |
| POST | `/sessions/{id}/confirm` | `{confirm_id, approve: bool}` | `{ok, confirm_id, approved}`; 404 unknown id, 409 already answered or expired |
| POST | `/sessions/{id}/cancel` | | `{ok, cancelled}`; Stop: the turn ends with `final` status `stopped` and any open confirmation counts as denied |

```jsonc
// CopilotSession
{"id": "cs_..", "title": "add 3 eval cases to repo-maintainer", "context": {"page": "/agents/x/evals",
 "agent_slug": "x"}, "created_at": 0.0, "updated_at": 0.0, "running": false}
// CopilotEvent: one stored step; the stream and `messages` carry the same objects
{"seq": 3, "type": "tool_result", "payload": {...}}
```

| type | payload |
|---|---|
| `user` | `{text, context}` |
| `assistant` | `{text, kind: "progress" or "final", tool_calls?: [{id, name, args}]}`; one per model turn (text per turn, not token deltas); model progress notes are `kind: "progress"` without `tool_calls` |
| `tool_call` | `{id, tool, args, risk}` |
| `tool_result` | `{id, tool, risk, ok, summary (one line for the UI), output (what the model saw, at most 6000 chars), links: [{label, href}] (relative app paths), refresh: ["evals:<slug>", "agent:<slug>", "runs:<slug>", "triggers:<slug>", "run:<id>", "agents"]}` (`agents`: the agents list changed) |
| `confirm_request` | `{id: "cf_..", tool (or "multiple"), args (one call's args, or a list), summary, items: [{call_id, tool, args, summary}]}` |
| `confirm_resolved` | `{id, approved, by: "user", "timeout" or "stopped"}` |
| `error` | `{message}` |
| `final` | `{status: "done", "stopped", "limit" or "error", message, steps, tokens_in, tokens_out, cost_usd}` |

Storage: tables `copilot_sessions` and `copilot_messages` (`CREATE TABLE IF NOT EXISTS`, so existing databases
gain them on connect). Ids `cs_` (session), `cm_` (message), `cf_` (confirmation).

### Model and metering

The model is `FORGE_GRADER_MODEL` through the provider adapters (Claude: adaptive thinking with progress
updates, no temperature, `tool_choice` auto). Every model call is metered in `grader_calls` with kind
`assist:copilot`: under the agent named by `context.agent_slug` (so it appears in that agent's metrics
`cost.assist.kinds.copilot` and counts toward its monthly budget), else under the bucket `agent_id = "copilot"`,
reported by `GET /metrics/overview` as `copilot: {calls, tokens_in, tokens_out, cost_usd, cost_complete}`. The
system prompt is static (cacheable); the page context goes into each user message as a `<context>` block.

### Tools

Thin wrappers over the HTTP API above (no business logic of their own), each with a JSON schema, a one-line
description and a risk. `read` tools run as soon as the model calls them; `write` tools change data or spend
money and run only after the user approves. No tool reads or writes secrets, keys, share tokens or webhook
secrets (the one exception: `get_deploy_info` hands the share link, which the Deploy tab also shows, to the UI as a
link the model never sees); the assistant refuses such requests, and every tool output and argument shown is passed through
redaction of all stored secret values.

| Tool | Risk | Wraps |
|---|---|---|
| `list_agents` | read | `GET /agents` |
| `get_agent` | read | `GET /agents/{slug}`: config summary of prod, versions, budget (no share token) |
| `list_runs` | read | `GET /agents/{slug}/runs` or `GET /runs` with status, version, source, q, limit (at most 25) |
| `get_run` | read | `GET /runs/{id}` + `events.json`: status, error, cost, input, output, eval verdict, condensed trace |
| `explain_failure` | read | as `get_run`, with a longer trace that keeps every error step |
| `list_eval_cases` | read | `GET /agents/{slug}/evals` |
| `get_eval_results` | read | `GET /agents/{slug}/evals/results` joined with case names (default prod) |
| `get_improvements` | read | `GET /agents/{slug}/improvements` or `GET /improvements/{id}` |
| `get_metrics` | read | `GET /agents/{slug}/metrics` or `GET /metrics/overview` |
| `docs_lookup` | read | keyword search over README, CONTRACT, TESTING and `docs/*.md` sections (`FORGE_DOCS_DIR` overrides the root) |
| `create_eval_case` | write | `POST /agents/{slug}/evals` (several calls in one turn share one confirmation) |
| `delete_eval_case` | write | `DELETE /evals/{id}` |
| `generate_eval_cases` | write | `POST /assist/<kind>` then `POST /agents/{slug}/evals` per case; assist kind `eval_cases` (`n` = count; `holdout` = count or 0 when a split is given) |
| `start_run` | write | `POST /agents/{slug}/runs` (with `generate_task`, the input comes from `POST /agents/{slug}/sample-task`) |
| `run_evaluation` | write | `POST /agents/{slug}/evals/run` |
| `start_improvement` | write | `POST /agents/{slug}/improve` |
| `promote_version` | write | `POST /agents/{slug}/promote` |
| `create_version` | write | `POST /agents/{slug}/versions` with system_prompt, model, tools, network or lessons (not promoted) |
| `set_approval_policy` | write | `POST /agents/{slug}/versions` with prod's rules plus the new ones (or `replace`), not promoted |
| `set_budget` | write | `PATCH /agents/{slug}` (monthly budget) and/or `POST /agents/{slug}/versions` (per-run cap) |
| `create_trigger` | write | `POST /agents/{slug}/triggers` with kind `schedule` only |
| `save_run_as_eval` | write | `POST /runs/{id}/save-as-eval` |
| `add_feedback` | write | `POST /runs/{id}/feedback` |
| `list_models` | read | `GET /models/available`: the server default plus each reachable provider's models (`\|qwen3:8b` hidden, as in the web presets) |
| `draft_agent` | read | `POST /agent-drafts`: one grader call that saves nothing; the result ends with a `config: {...}` line (slug, name, description, system_prompt, tools, network, eval_cases) for `create_agent`, and lists suggested secrets by name only |
| `get_deploy_info` | read | `GET /agents/{slug}`: runs and sessions endpoints and a curl example on `public_url` (else `$FORGE_URL`); the API key is never shown; the share link `/s/{slug}?share=<token>` is only in `links`, never in the text the model sees |
| `create_agent` | write | `POST /agents` with slug, name, description, system_prompt, model, tools, network, seed_files, suggested_prompts, eval_cases, auto_promote; the returned `api_key` is dropped |
| `add_example_agents` | write | `POST /examples/{key}/install`, or without a key `POST /examples/install-all`, optional `model`; `api_key`s dropped |
| `add_http_tool` | write | `POST /agents/{slug}/versions` with prod's `http_tools` plus the new one (not promoted) |
| `add_mcp_server` | write | `POST /agents/{slug}/versions` with prod's `mcp_servers` plus the new one (not promoted) |
| `rollback_version` | write | `POST /agents/{slug}/promote` with the version that was prod before the current one (from the promotion log, else the highest lower version number) |

Models: `create_agent`, `create_version` and `add_example_agents` accept a model only when the app can run it now:
`anthropic:<id>`, `openai_compat:<base_url>|<model>` or `fake:<scenario>`, and `model_warning` (the New agent
form's warning) must be empty, so a Claude model needs a configured key that offers it and a local model must be
installed on its reachable endpoint. Omitted, the server default applies.

Credentials: `add_http_tool` headers and `add_mcp_server` headers and env may reference secrets only as
`{{NAME}}`; a value under a credential-like name (auth, token, key, secret, password, cookie, session) without a
reference, a value that looks like a pasted key (`sk-`, `ghp_`, `xoxb-`, `AKIA`, `Bearer <long literal>`) or a
URL with `api_key=` and similar is refused before the user is asked. Secret values are set by the user on the
Configure tab only.

Settings are not exposed: this base has no settings registry.

### Confirmation flow

1. In each model turn, read calls run in order; every write call is validated (schema, then the tool's own
   check, e.g. the agent and version exist) and an invalid one returns an error result without asking.
2. The valid write calls of that turn are announced together in ONE `confirm_request` whose `summary` and
   per-item `summary` say exactly what changes and what it may cost (estimated from the agent's recent average
   run cost, the number of missing eval runs and the grader price). The loop waits.
3. `POST /confirm` with `approve: true` runs them in order (`tool_call`, `tool_result` each); `false` returns an
   error result to the model for each ("the user denied this action, so nothing was changed"), and the model is
   told not to retry. No answer within 10 minutes counts as denied (`by: "timeout"`). The engine's single
   execution path refuses a write tool without an approval.

### Limits

Per user message: at most 15 tool calls (further calls get an error result and the turn ends with `final`
status `limit`), 2 minutes of wall clock excluding time spent waiting on a confirmation and running the
approved actions, 20 model calls; each
tool output is capped at 6000 characters for the model, older history outputs at 1500; user text at 8000
characters. One turn runs per session at a time; it keeps running when the stream disconnects.

### Web

`Assistant` nav button (sparkles icon) and Cmd+J or Ctrl+J toggle a right side panel, resizable from 360 to 520 px
(a full screen sheet under 768 px); open state, width and current session are kept in `localStorage`. The header
shows the context (`Repo maintainer · Evaluations`), recent sessions (10) and New session. Messages render as
user bubbles, assistant Markdown (app links navigate in place), compact tool steps (`● tool(args) ⎿ summary`),
confirm cards (Approve, Deny, Details) and result links. Each successful tool result's `refresh` keys are
published as a `forge:refresh` window event; the Evaluations tab, Runs tab and agent header refetch. Enter
sends, Shift+Enter adds a line, Esc or the Stop button stops. Starters depend on the page.

### Test model

`fake:copilot` (also `fake:grader` given the assistant's system prompt): see TESTING.md.
