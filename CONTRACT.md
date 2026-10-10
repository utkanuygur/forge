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
