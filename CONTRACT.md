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
 "prod_version": 1, "auto_promote": false, "share_token": "..", "created_at": 0.0,
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
 "turns": 7, "tokens_in": 0, "tokens_out": 0, "has_artifacts": true,
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
            "base": {"passed": false, "reason": "..", "run_id": "r_.."},
            "candidate": {"passed": true, "reason": "..", "run_id": "r_.."}}],
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
| PATCH | `/agents/{slug}` | `{name?, description?, auto_promote?}` | `Agent` |
| POST | `/agents/{slug}/versions` | `{system_prompt?, lessons?, model?, tools?, http_tools?, mcp_servers?, callable_agents?, network?, seed_files?, rationale?}` (unset fields copied from prod; 400 when the tool configuration is invalid) | `Version` |
| GET | `/agents/{slug}/versions/{n or "prod"}` | | `Version` |
| POST | `/agents/{slug}/promote` | `{version: n}` | `Agent` |
| PUT | `/agents/{slug}/secrets` | `{NAME: value, ...}` | `{"names": [..]}` |
| POST | `/agents/{slug}/rotate-key` | | `{"api_key": ".."}` |
| GET | `/templates` | | `[{key, name, description, system_prompt, tools, network}]` |
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
| GET | `/agents/{slug}/evals/results?version=n` | | `{"version": n, "train": {...}, "holdout": {...}, "results": [{case_id, passed, reason, run_id}]}` |
| POST | `/agents/{slug}/improve` | `{}` | `Improvement` (status `running`; poll) |
| GET | `/improvements/{id}` | | `Improvement` |
| GET | `/agents/{slug}/improvements` | | `[Improvement]` newest first |

### Local runner

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/runs` | `{agent_slug, version_n, input, source: "local"}` | `Run` (status `running`) |
| POST | `/runs/{id}/events` | `[Event]` | `{"ok": true}` (a `run_finished` event finalizes the run) |

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
  semaphore because the parent already holds a slot. When a child finishes, its `tokens_in`/`tokens_out` plus its
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
`runs.child_tokens_in`, `runs.child_tokens_out`, `runs.child_cost_usd` (rolls up a child's `cost_usd` column when
one exists).

## Promotion rule

Candidate eligible iff `candidate_passed_total > base_passed_total` and no case passed by base fails for candidate.
`auto_promote` agents: eligible candidates are promoted automatically; an improvement cycle also starts when 3 new
negative feedback items arrive since the last improvement.
