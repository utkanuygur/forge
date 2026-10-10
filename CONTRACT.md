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
 "prod_version": 1, "auto_promote": false, "eval_repeats": 3, "share_token": "..", "created_at": 0.0,
 "versions": [Version, ...]}            // versions only on GET /agents/{slug}

// Version  (AgentVersion fields, JSON)
{"id": "v_..", "agent_id": "ag_..", "n": 1, "system_prompt": "..", "lessons": ["..."],
 "model": "anthropic:claude-sonnet-5-5", "tools": ["bash","read_file","write_file","edit_file"],
 "http_tools": [], "network": false, "seed_files": {"path": "content"},
 "parent_id": null, "created_by": "human", "rationale": "", "created_at": 0.0}

// Run
{"id": "r_..", "agent_id": "ag_..", "version_id": "v_..", "version_n": 1, "source": "api",
 "input": "..", "status": "succeeded", "output": "..", "error": null,
 "turns": 7, "tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
 "has_artifacts": true,
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
| POST | `/agents` | `{slug, name, description?, system_prompt, model?, tools?, network?, seed_files?, auto_promote?}` | `Agent` + `"api_key"` (shown once) |
| GET | `/agents` | | `[Agent]` |
| GET | `/agents/{slug}` | | `Agent` with `versions` |
| PATCH | `/agents/{slug}` | `{name?, description?, auto_promote?, eval_repeats?: 1..9}` | `Agent` |
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
| GET | `/agents/{slug}/evals/results?version=n` | | `{"version": n, "train": {...}, "holdout": {...}, "repeats": k, "results": [{case_id, passed, reason, run_id, passes, runs}]}` |
| POST | `/agents/{slug}/improve` | `{}` | `Improvement` (status `running`; poll) |
| GET | `/improvements/{id}` | | `Improvement` |
| GET | `/agents/{slug}/improvements` | | `[Improvement]` newest first |

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
| `tool_result` | `duration_ms` (tool execution time) |
| `error` | `duration_ms` when the model call itself failed; for a refused turn (see "Stop reasons") also `turn`, `duration_ms`, `tokens_in`, `tokens_out` |

The per-turn usage on `assistant`, `tool_call` and refusal `error` events also carries `cache_read_tokens` and
`cache_write_tokens` when they are nonzero.

### Additive Run fields

`duration_ms` (end minus start, null until finished), `cost_usd` (null when the model's price is unknown) and
`cost_note` (`""`, `"self-hosted, not metered"` or `"unknown price"`). List endpoints also include `agent_slug`.

### Pricing

USD per million tokens (input / output): `claude-opus-5-5` 4 / 20, `claude-sonnet-5-5` 2 / 10, `claude-haiku-5-5`
0.10 / 0.50, `claude-opus-5` 5 / 25, `claude-sonnet-5` 2 / 10, `claude-opus-4-8` 5 / 25, `claude-sonnet-4-6` 3 / 15,
`claude-haiku-4-5` 1 / 5. `openai_compat:` and `fake:` models cost 0 and are labeled self-hosted (GPU time is billed
by the GPU provider). Unknown `anthropic:` models have a null cost. `FORGE_PRICES` (JSON) overrides or extends the
table: keys are full model strings or bare Anthropic ids, values `[input, output]` or `{"input": x, "output": y}`.
Run cost uses `runs.tokens_in/out` and the run's version model. Prompt-cache tokens (`cache_read_tokens`,
`cache_write_tokens`) are not priced yet: `tokens_in` is the uncached input count, and cache counts are reported
separately (Run objects, timelines, Health, per-version cost rows) until cache multipliers are documented here.

### Metric definitions

- Production metrics exclude `source = "eval"` runs unless `include_eval=true`.
- Success rate = succeeded / finished (succeeded, failed, timeout). Duration = `ended_at - started_at`; p50 and p95
  use linear interpolation. Thumbs up rate = +1 feedback items / all rated items.
- Periods: `days` back from now; "previous" is the equally long period before it. Buckets are hourly for 2 days
  or less, daily otherwise, aligned to the client's local midnight via `tz_offset_min` (minutes east of UTC).
- Failure categories: `timeout`, `model_error`, `sandbox_error` (executor, runtime or interrupted), `tool_errors`
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
| GET | `/runs/{id}/timeline` | `{run_id, status, estimated, total_ms, model_ms, tool_ms, other_ms, queue_ms, turns, tool_calls, spans: [Span], tokens_in, tokens_out, cache_read_tokens, cache_write_tokens, cost_usd, cost_note, price}` |
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

Timelines for runs recorded before the timing fields existed are built from event timestamps (`estimated: true`):
a model turn spans from the previous step to the event that reports it, a tool call from `tool_call` to `tool_result`.
Improvement cycle cost = its eval runs (all k repeated runs of each case, linked when the cycle starts them;
stored runs reused from earlier evals are not re-counted) plus its judge and optimizer calls (one judge call per
judged run).

### Ops (no `/v1` prefix)

| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/healthz` | none | `{"status": "ok" or "error", "executor", "components": {"database": {...}, "docker": {...}}, "time"}`; 200 when every component is ok or skipped, else 503. Docker (daemon and `FORGE_SANDBOX_IMAGE`) is checked only when `FORGE_EXECUTOR=docker` (the default), cached 10 s. |
| GET | `/metrics` | localhost unless `FORGE_METRICS_PUBLIC=1` | Prometheus text: `forge_runs_total{agent,status,source}`, `forge_run_duration_seconds` histogram `{agent}`, `forge_tokens_total{agent,direction}`, `forge_cost_usd_total{agent}`, `forge_tool_calls_total{tool,error}`, `forge_active_runs`, `forge_improvements_total{status}` |

Structured logs: logger `forge.lifecycle` writes one JSON line per run event (`run_queued`, `run_started`,
`run_finished` with status, duration_ms, tokens and cost_usd) carrying `run_id` and `agent`. On when
`FORGE_LOG_JSON=1`, off when `0`; when unset, on unless stderr is a TTY.

### Additive schema

`promotions` (every prod pointer change: create, human, auto), `grader_calls` (judge and optimizer token usage,
with `improvement_id` and the judged `run_id`), `improvement_runs` (eval runs started by a cycle) and an index on
`runs(created_at)`. All are `CREATE ... IF NOT EXISTS`, so existing databases upgrade in place (columns added after the first schema,
such as `runs.cache_read_tokens`, go through `forge.db.ADDED_COLUMNS`, applied by `_migrate` on connect). Promotions before
this change are not known; an agent without records shows one synthetic promotion at its creation time.
