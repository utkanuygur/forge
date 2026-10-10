# Forge technology stack

Read from the repository on 2026-10-10: master at `5af0139` plus the unmerged branches feat/governance, feat/tools,
feat/triggers, feat/run-view, feat/playground-extras, feat/generate-task, feat/example-loader, feat/sync-docs,
feat/parallel-tests, feat/settings, feat/ci-split and integrate/batch (governance + tools + triggers merged with
master). Versions are the exact resolved versions in `uv.lock`, `web/package-lock.json` and
`desktop/package-lock.json`, not the ranges in the manifests.

## 1. Summary

Forge is one Python 3.12 process: a FastAPI app served by uvicorn that stores everything in a single SQLite file
(WAL mode), runs its own agent loop against Claude (anthropic SDK) or any OpenAI compatible endpoint (openai SDK:
Ollama locally, vLLM on a Modal H100 for Qwen3 Coder), and executes every tool call inside a disposable Docker
container (`forge-sandbox`, no network by default, 1 CPU, 1 GB, 256 pids). The same process runs evaluations, an
LLM judge, a lesson optimizer with a no regression promotion rule, and observability computed from its own
database. A React 18 + TypeScript + Vite + Tailwind single page app is built into `web/dist` and served by the same
server; a Typer CLI runs agents on the user's machine; an Electron shell packages the PyInstaller frozen server as a
macOS app. Tests replace only the model (scripted `fake:` scenarios) and keep server, database, Docker, browser and
CLI real; CI is GitHub Actions. Most of the hard parts (loop, sandboxing, evals, optimizer, pruning, metrics, cron,
webhook signatures, SSRF guard, terminal renderer) are written in the repo; dependencies are kept to SDKs, the web
framework and the UI toolkit.

```
                         SURFACES
  +-------------+  +-------------+  +-------------+  +---------------+  +------------------+
  | Web SPA     |  | Share page  |  | REST API    |  | forge CLI     |  | Triggers [pend.] |
  | React/Vite  |  | /s/{slug}   |  | Bearer key  |  | typer + httpx |  | cron, GitHub,    |
  | (web/dist)  |  | ?share=tok  |  | /v1/...     |  | LocalExecutor |  | Slack (HMAC)     |
  +------+------+  +------+------+  +------+------+  +-------+-------+  +--------+---------+
         |                |                |                 |                   |
         +----------------+-------+--------+-----------------+-------------------+
                                  |  HTTP + SSE (sse-starlette)
  +-------------------------------v---------------------------------------------------------+
  | FORGE SERVER  forge.server.app:app  (FastAPI 0.143.0 on uvicorn 0.54.0, Python 3.12)    |
  |  auth.py (key, share token, localhost builder)   runs.py (RunManager, semaphore 8)      |
  |  loop.py (agent loop, 4 builtin tools)           evals.py + improve.py + pruning.py     |
  |  drafting.py / assist [pend.]                    observability.py, obslog.py, pricing.py |
  |  tools/ registry: HTTP, MCP, agents [pend.]      governance: approvals, caps [pend.]     |
  |  triggers/ scheduler + hooks [pend.]             settings registry [pend.]               |
  +-----+------------------------+---------------------------+------------------------------+
        |                        |                           |
        | docker run/exec        | provider adapters         | sqlite3 (one conn per call)
  +-----v-----------------+  +---v-------------------------+ +v-------------------------------+
  | SANDBOX               |  | MODEL PROVIDERS             | | STORAGE  $FORGE_DATA_DIR       |
  | forge-sandbox image   |  | anthropic:<id>  Claude API  | |  forge.db (SQLite, WAL)         |
  | python:3.12-slim      |  | openai_compat:<url>|<model> | |  runs/<id>/workspace.tar.gz     |
  | --network none|bridge |  |   Ollama (qwen3:8b, local)  | |  runs/<id>/diff.patch           |
  | 1 cpu, 1g, 256 pids   |  |   vLLM on Modal H100        | +--------------------------------+
  | user agent, git base  |  |   (Qwen3-Coder-30B-A3B)     |
  +-----------------------+  | fake:<scenario> (tests)     |
                             +-----------------------------+
  DESKTOP SHELL (macOS arm64)                         CI (GitHub Actions, ubuntu-latest)
  Electron 44.4.4 main process                        uv sync --locked, npm ci, docker build,
   -> PyInstaller forge-server on 127.0.0.1           pytest -m "not live", vitest, playwright,
   -> Docker Desktop check, image build               demo/verify.py
   -> safeStorage (Keychain) API key                  [pend. feat/ci-split: 9 parallel jobs,
   -> cloudflared quick tunnel                         macos-14 desktop compat build]
```

## 2. Layer by layer

Status: **master** means merged. **pending: X** means only on branch X (and also in integrate/batch where noted).

### Runtime, server and storage

| Layer | Technology (exact version) | What it does in Forge | Why chosen | Alternatives considered | Where in the repo | Status |
|---|---|---|---|---|---|---|
| Language | Python 3.12 (`requires-python >=3.12`) | Server, loop, evals, CLI | Async I/O, SDKs for every provider, one language for backend and CLI | Not recorded in repo | `pyproject.toml` | master |
| Package manager | uv (lockfile `uv.lock`, 47 packages) | `uv sync`, `uv run`, `uv sync --locked` in CI, managed CPython for the desktop build | Fast, reproducible lock, ships prebuilt CPython | Not recorded in repo | `pyproject.toml`, `uv.lock`, `Makefile` | master |
| Build backend | hatchling | Builds the `forge` package and the `forge` console script | Default for uv projects | Not recorded in repo | `pyproject.toml` | master |
| Node | Node 22 in CI (`actions/setup-node`), README says 20+, desktop README says 22+ | Builds and tests `web/`, runs desktop tooling | Required by Vite 5, Vitest, Playwright, Electron tooling | Not recorded in repo | `.github/workflows/ci.yml`, `README.md` | master |
| Web framework | FastAPI 0.143.0 on Starlette 1.7.0 | All `/v1` routes, ops routes, SPA catch all, CORS for the Vite dev server | Async, pydantic models as request bodies, small | Not recorded in repo | `forge/server/app.py` | master |
| ASGI server | uvicorn[standard] 0.54.0 (uvloop 0.23.0, httptools 0.9.0, watchfiles 1.3.0, websockets 17.2) | Serves the app (`make serve`, `make dev --reload`, desktop entry) | Standard FastAPI server | Not recorded in repo | `Makefile`, `forge/desktop_entry.py` | master |
| Streaming | sse-starlette 3.5.0 | `GET /v1/runs/{id}/events` live trace with resume (`after`), ping every 15 s | Server push without websockets; browser `EventSource` | Websockets (not recorded) | `forge/server/app.py` | master |
| Validation | pydantic 2.14.0 (pydantic-core 2.50.0) | Contract types, request bodies, lesson edits, policy models | Same models for API and internals | Not recorded in repo | `forge/types.py`, `forge/improve.py` | master |
| Uploads | python-multipart 0.0.32 | Multipart run inputs and `.tar.gz` seed uploads | Required by FastAPI forms | Not recorded in repo | `forge/server/app.py` | master |
| Config | python-dotenv 1.2.4 | Loads `.env` for server, CLI and preflight | Simple local config | Not recorded in repo | `forge/server/app.py`, `forge/cli.py` | master |
| Settings registry | Own code (`register_setting`, env > DB > default) | Settings page, model availability, eval early stop | One declarative list for UI and env | Env only (status quo) | `forge/settings.py`, `forge/models_available.py` | pending: feat/settings |
| Storage | SQLite via stdlib `sqlite3`, `PRAGMA journal_mode = WAL`, `foreign_keys = ON` | All state: agents, versions, runs, events, evals, improvements, secrets | Zero ops, one file, enough for one machine (NFR7) | Postgres (scale item, not built) | `forge/db.py`, `forge/schema.sql` | master |
| Connections | One short lived connection per call, `timeout=30`, autocommit | Sync functions called from async code | Simple and safe with WAL | Connection pool, async driver | `forge/db.py` | master |
| Migrations | Idempotent `CREATE TABLE IF NOT EXISTS` plus `ADDED_COLUMNS` dict applied with `ALTER TABLE ADD COLUMN` on first connect | Additive schema changes only | No migration tool needed for additive changes | Alembic (not used) | `forge/db.py` (`_migrate`) | master |
| Artifacts | Files under `$FORGE_DATA_DIR/runs/<id>/` | Workspace tarball and git diff per run | Plain files next to the DB | Object storage (scale item) | `forge/db.py`, `forge/executors.py` | master |

### Agent runtime and sandbox

| Layer | Technology (exact version) | What it does in Forge | Why chosen | Alternatives considered | Where in the repo | Status |
|---|---|---|---|---|---|---|
| Agent loop | Own code `run_agent` | Model and tools until a reply without tool calls; 30 turns, 600 s, outputs truncated to 10,000 chars, secrets redacted | Full control of tracing, limits and stop reasons | Agent frameworks (not used) | `forge/loop.py` | master |
| Builtin tools | Own code: `bash`, `read_file`, `write_file`, `edit_file` | Tool specs plus minimal arg check; bash timeout 1 to 120 s | Enough for code and file agents | Not recorded in repo | `forge/loop.py` (`TOOL_SPECS`) | master |
| Executors | Own `DockerExecutor` and `LocalExecutor` | Docker: one container per run; Local: host dir with ask mode for the CLI; both export tarball and diff from a git baseline | Same loop for hosted and local runs | Not recorded in repo | `forge/executors.py` | master |
| Run manager | Own `RunManager`, asyncio semaphore (`FORGE_MAX_CONCURRENCY`, default 8) | Queues runs, writes events, posts `callback_url` webhook with 3 retries | In process, no queue service | External queue (scale item) | `forge/server/runs.py` | master |
| Sandbox image | Docker, `FROM python:3.12-slim`; apt: git, ripgrep, make, curl, bash, ca-certificates, nodejs, npm, procps; pip (unpinned): pytest, fastapi, httpx, uvicorn, ruff, pydantic | Runtime for every tool call, tooling baked in so offline runs work | Containers isolate without a VM; image covers the demo repos | OrbStack and other runtimes untested (docs/tdd-updates.md M) | `sandbox/Dockerfile` | master |
| Sandbox limits | `--cpus 1 --memory 1g --pids-limit 256 --user agent -w /workspace`, in container `timeout --kill-after=2` | Bounds a run; output capped at 256 KB per stream | Simple Docker flags | gVisor, Firecracker (not used) | `forge/executors.py` | master |
| Network policy | `--network none` by default, `bridge` when the version sets `network` | Egress on or off per version | Binary policy is easy to reason about | Egress allowlists (future work in TDD) | `forge/executors.py` | master |
| Sessions and cancel | Own code: multi turn Playground sessions, `POST /runs/{id}/cancel` | Each turn continues from the previous workspace | Iterate without new versions | Not recorded in repo | `forge/sessions.py`, `forge/server/sessions_api.py` | pending: feat/run-view |
| Workspace browsing | Own tar reader, read only, 1 MB text cap, `.git` hidden | `GET /runs/{id}/files[/{path}]` | Nothing extracted to disk | Not recorded in repo | `forge/server/files.py` | pending: feat/playground-extras |

### Model providers

| Layer | Technology (exact version) | What it does in Forge | Why chosen | Alternatives considered | Where in the repo | Status |
|---|---|---|---|---|---|---|
| Claude | anthropic 1.13.0, `AsyncAnthropic(max_retries=0)` | Default agent model `anthropic:claude-sonnet-5-5`; grader via `FORGE_GRADER_MODEL` (`.env.example`: `claude-opus-5-5`) | Strongest tool use; first class SDK | Not recorded in repo | `forge/providers.py`, `forge/server/evals.py` | master |
| Prompt caching | Ephemeral `cache_control` on the system block and, in tool loops, the last message block (2 of 4 breakpoints) | Cache read and write tokens recorded per turn and run; not priced | Agent turns resend a growing prefix | No caching (D17 in tdd-updates.md) | `forge/providers.py` | master |
| Stop reasons | Normalized `stop_reason`; refusal fails the run, `max_tokens` without tools fails, truncated last tool call not run; `MAX_TOKENS = 16000` | Safe handling of cut off replies | Never accept a truncated answer | Not recorded in repo | `forge/providers.py`, `forge/loop.py` | master |
| Sampling | `temperature` sent only to models matching `accepts_temperature` (pre Opus 4.7 / Sonnet 5) | Current Claude models reject sampling params | Avoids 400s | Not recorded in repo | `forge/providers.py` | master |
| Retries | Own `with_retry`, backoff 1, 2, 4 s on 408, 409, 429, 5xx, 529, timeouts | Both SDKs run with their own retries off | One policy for all providers | SDK retries (turned off) | `forge/providers.py` | master |
| Structured output | Claude `output_config.format` json_schema; lenient JSON parsing elsewhere | Agent drafting (and assist on its branch) | Valid drafts without parsing tricks | Not recorded in repo | `forge/drafting.py` | master |
| Progress updates | Beta `thinking-display-updates-2026-08-18`, `thinking: {type: adaptive, display: updates}` for opus-5-5, sonnet-5-5, fable-5, fable-5-1, mythos-5-1 | Narration between tool calls as `assistant` events with `kind: progress` | Claude Code style transcript | Not recorded in repo | `forge/providers.py` on the branch, `forge/transcript.py` | pending: feat/run-view |
| OpenAI compatible | openai 3.28.0, `AsyncOpenAI(base_url=..., max_retries=0)` | `openai_compat:<base_url>|<model>`; strips `<think>`, maps `finish_reason` | One adapter for Ollama, vLLM, anything OpenAI shaped | Per vendor adapters | `forge/providers.py` | master |
| Local model | Ollama, `qwen3:8b` at `http://localhost:11434/v1` | Agents on the user's machine without an API key | Easy local install | Any OpenAI compatible server | `README.md` (feat/sync-docs), web model presets | master (docs on feat/sync-docs) |
| GPU model | vLLM 0.10.2 on Modal, H100, `Qwen/Qwen3-Coder-30B-A3B-Instruct` served as `qwen3-coder`, `--tool-call-parser qwen3_coder`, `--enable-auto-tool-choice`, `--max-model-len 65536`, 32 concurrent inputs, 15 min scaledown | Open source coding model with tool calls | vLLM: continuous batching and builtin tool parsers; Modal: per second GPU billing, no cluster | Not recorded beyond the docstring | `gpu/vllm_modal.py` | master |
| Test models | Own `ScenarioProvider` (`fake:hello`, `echo`, `lessons`, `grader`, `loop`, `error`, `flaky`) and `FakeProvider` | Deterministic runs, judge, optimizer and drafting | Keeps every other component real | Mocks (rejected in TESTING.md) | `forge/providers.py` | master |
| Model availability | httpx GET `/v1/models` per provider, 60 s cache | Settings page and picker warnings | No SDK call needed | Not recorded in repo | `forge/models_available.py` | pending: feat/settings |

### Improvement system and observability

| Layer | Technology (exact version) | What it does in Forge | Why chosen | Alternatives considered | Where in the repo | Status |
|---|---|---|---|---|---|---|
| Evals | Own runner: shell checkers in the sandbox and judge rubrics; train and holdout splits; 4 concurrent eval runs | Scores a version per case, cached per version | Reproducible, cheap to rerun | Not recorded in repo | `forge/server/evals.py`, `forge/improve.py` | master |
| Majority of k | Each case runs k times (`eval_repeats` 1 to 9, `FORGE_EVAL_REPEATS`, default 3); strict majority passes | Dampens model nondeterminism | Simple, keeps the promotion rule | Confidence intervals, pass@k (D16) | `forge/improve.py` (`majority`), `forge/server/evals.py` | master |
| Judge | Grader model returns `{"passed", "reason"}` | Grades rubric cases from task, output and diff | Rubrics cover what shell checks cannot | Not recorded in repo | `forge/improve.py` (`judge`) | master |
| Optimizer | Grader proposes lesson edits (add, edit, remove) with evidence run IDs; max 15 lessons, 300 chars, 6 gram leak check against checkers | Turns failures and feedback into a candidate version | Evidence linked, auditable lessons | Few shot optimization (future work) | `forge/improve.py` | master |
| Promotion rule | Candidate must pass strictly more cases with zero regressions, incl. holdout; agents with `auto_promote` start an improvement after 3 new negative feedbacks and promote an eligible candidate | Gate for new versions | No silent regressions | Not recorded in repo | `forge/improve.py` (`is_eligible`) | master |
| Pruning | Leave one out ablation on train cases, up to 8 lessons oldest first; tie accepted; never auto promoted | Retires lessons that never helped | Does not trust the model to judge its own lessons | Optimizer proposed removals (D19) | `forge/pruning.py`, `forge/server/authoring.py` | master |
| Provenance | `lesson_meta` per lesson: version, evidence, origin, pruning verdict | Shown in Configure | Audit trail | Not recorded in repo | `forge/provenance.py` | master |
| Drafting | One grader call with structured output, validated, retried once | "Describe your agent" creates a reviewable draft and starter evals | Speeds up a blank page | Creating directly (D18) | `forge/drafting.py`, `forge/server/authoring.py` | master |
| Assist (Generate) | Own assist registry, kind `task`, `POST /v1/assist/{kind}`, share tokens 10 per hour | Fills the task box with a realistic sample task | One seam for future Generate buttons | Not recorded in repo | `forge/assist/`, `forge/server/assist.py` | pending: feat/generate-task |
| Metrics and cost | Own SQL over the DB; prices in USD per million tokens, `FORGE_PRICES` override; self hosted models free | Dashboard, per agent monitoring, failure breakdown, tool stats, run search | Nothing sent to third parties | LangSmith, OpenTelemetry export (future work) | `forge/server/observability.py`, `forge/pricing.py`, `forge/metrics.py` | master |
| Ops | `/healthz` (DB and Docker image, 503 on failure); hand written Prometheus text at `/metrics` (localhost unless `FORGE_METRICS_PUBLIC=1`); JSON lifecycle logs (`FORGE_LOG_JSON`) | Health and scraping | No prometheus_client dependency | prometheus_client (not used) | `forge/server/observability.py`, `forge/server/obslog.py` | master |

### Governance, tools, triggers

| Layer | Technology (exact version) | What it does in Forge | Why chosen | Alternatives considered | Where in the repo | Status |
|---|---|---|---|---|---|---|
| Approvals | Own policy engine: rules on bash regex or file path glob, mode `ask` or `deny`, 15 min timeout | Pauses a run in `awaiting_approval` until a human decides | Share risky agents without full trust | Blocking risky tools entirely (D20) | `forge/policy.py`, `forge/governance.py`, `forge/server/governance.py` | pending: feat/governance (in integrate/batch) |
| Spending caps | Own `budget.py`: per run cap, monthly agent budget, 80% warning, 402 at budget | Ends runs `budget_exceeded` | Costs are already metered | Not recorded in repo | `forge/budget.py` | pending: feat/governance (in integrate/batch) |
| Tool registry | Own `ToolSource` protocol, names `mcp__<server>__<tool>` and `agent__<slug>` | One seam for non builtin tools | Loop stays unaware of sources | Not recorded in repo | `forge/tools/registry.py` | pending: feat/tools (in integrate/batch) |
| HTTP tools | httpx 0.28.1 + jsonschema 4.26.0 (Draft 2020-12), URL templates, `{{SECRET}}` headers, 5 redirects, 2 MB body cap | Typed HTTP calls from the server process | Schema validated arguments | Not recorded in repo | `forge/tools/http.py` | pending: feat/tools (in integrate/batch) |
| MCP | mcp 2.3.0 (official Python SDK, adds mcp-types, pyjwt, cryptography) over Streamable HTTP, SSE or stdio; stdio runs inside the sandbox via `docker exec -i` | Tools from MCP servers | Reuse the ecosystem without host access | Custom adapters per service (D21) | `forge/tools/mcp.py` | pending: feat/tools (in integrate/batch) |
| Callable agents | Own: child run of the callee's prod version, depth 3, no cycles, usage rolled up | Agents calling agents | Reuses the run manager | Not recorded in repo | `forge/tools/agents.py` | pending: feat/tools (in integrate/batch) |
| Cron | Own 5 field parser with macros, Vixie day rule, IANA zones via stdlib `zoneinfo`, DST rules | Scheduled triggers; web preview mirrors it in TypeScript | No dependency, exact DST semantics | croniter (not used) | `forge/triggers/cron.py`, `web/src/lib/cron.ts` | pending: feat/triggers (in integrate/batch) |
| Webhooks in | Own HMAC SHA256: GitHub `X-Hub-Signature-256`, Slack `v0` with 5 min window; constant time compare; per trigger rate limit 30 per hour | GitHub and Slack triggered runs with replies | Stdlib `hmac` is enough | Not recorded in repo | `forge/triggers/security.py`, `github.py`, `slack.py` | pending: feat/triggers (in integrate/batch) |
| Scheduler | Own asyncio loop, 15 s interval, slot advanced before the run, skips slots over 10 min late | Fires schedules at most once | In process | External scheduler | `forge/triggers/scheduler.py` | pending: feat/triggers (in integrate/batch) |

### Clients, desktop, delivery

| Layer | Technology (exact version) | What it does in Forge | Why chosen | Alternatives considered | Where in the repo | Status |
|---|---|---|---|---|---|---|
| Web UI | React 18.3.1, react-dom 18.3.1 | Single page app: Agents, Dashboard, agent tabs, run view, share page | Common, fast to build | Not recorded in repo | `web/src/` | master |
| Language | TypeScript 5.6.3 (`tsc -b` in build and lint) | Typed UI and API client | Catches contract drift | Not recorded in repo | `web/tsconfig.json` | master |
| Bundler | Vite 5.4.21 (+ @vitejs/plugin-react 4.7.0, esbuild 0.21.5, rollup 4.64.5) | Dev server on 5173 with `/v1` proxy, build to `web/dist` | Fast dev loop | Not recorded in repo | `web/vite.config.ts` | master |
| Styling | Tailwind CSS 3.4.19, PostCSS 8.5.29, autoprefixer 10.6.1 | All styling | Utility classes, no CSS framework runtime | Not recorded in repo | `web/tailwind.config.js` | master |
| Routing | react-router-dom 6.30.6 | Client routes, SPA catch all on the server | Standard | Not recorded in repo | `web/src/App.tsx` | master |
| Icons | lucide-react 0.460.0 | All icons | Tree shakable SVG icons | Not recorded in repo | `web/src/` | master |
| Charts, SSE client | Own code (no chart or state library) | Dashboard charts, `useRunEvents` live trace | Few deps | Not recorded in repo | `web/src/components/` | master |
| Markdown | react-markdown 9.1.0, remark-gfm 4.0.1 | Transcript and final answers | GFM tables and lists | Not recorded in repo | `web/src/components/transcript/Markdown.tsx` | pending: feat/run-view |
| Highlighting | rehype-highlight 7.0.2, lowlight 3.3.0 (highlight.js 11.11.2), hast-util-to-jsx-runtime 2.3.6; 15 grammars | Code blocks and diffs in the transcript | Small fixed grammar set | Own highlighter (feat/playground-extras has one) | `web/src/components/transcript/highlight.tsx` | pending: feat/run-view |
| File viewer highlighting | Own token highlighter, no HTML output | Workspace file browser | Dependency free, safe for arbitrary files | lowlight (run-view) | `web/src/lib/highlight.ts` | pending: feat/playground-extras |
| CLI | typer 0.27.3 (+ click 8.5.0, rich 15.0.0, shellingham), httpx 0.28.1 | `forge login`, `run`, `feedback`, `sync`; offline outbox | Typed commands with little code | Not recorded in repo | `forge/cli.py` | master |
| CLI chat | Own ANSI renderer (honors `NO_COLOR`, TTY detection) | `forge chat <slug>[@vN]` REPL over sessions | No extra dependency | rich rendering (not used) | `forge/cli_render.py` | pending: feat/playground-extras |
| Desktop shell | Electron 44.4.4 | macOS app: Docker check, image build, server lifecycle, windows, menus | One download for non developers, same server | Native Swift shell (D22) | `desktop/src/` | master |
| Packaging | electron-builder 26.16.1: arm64 dmg `Forge-${version}-${arch}.dmg`, `minimumSystemVersion 12.0`, asar, ad hoc signed via afterPack, not notarized | Builds the dmg | Standard Electron packager | Not recorded in repo | `desktop/package.json`, `desktop/scripts/` | master |
| Frozen server | PyInstaller (unpinned, via `uv run --with pyinstaller`), onedir arm64, uv managed CPython 3.12, `MACOSX_DEPLOYMENT_TARGET=12.0`, `check-min-macos.sh` (otool) | Server binary inside the app | No Python on the user's Mac | Asking users to install uv (D22) | `desktop/scripts/build-server.sh`, `forge/desktop_entry.py` | master |
| Desktop secrets | Electron `safeStorage` (Keychain item "Forge Safe Storage"), only ciphertext on disk | Claude API key | OS keychain | Plain settings file | `desktop/src/settings.js` | master |
| Desktop verify | playwright-core 1.62.1 `_electron` | Headless app test: Docker, server, encrypted key, `fake:hello` run, no leftover processes | Same tool as web e2e | Not recorded in repo | `desktop/scripts/verify.mjs` | master |
| Tunnel | cloudflared quick tunnel (user installed, not bundled) | Public `trycloudflare.com` link for share pages and API | No account or DNS needed | Not recorded in repo | `desktop/src/tunnel.js`, `README.md` | master |
| Distribution | GitHub Releases on the private repo `utkanuygur/forge`, one tag per version (v0.1.1, v0.1.2) with dmg, notes, SHA-256 | Delivers the app | Private source, no store | Mac App Store, notarized download (not done) | `desktop/README.md` (feat/sync-docs) | master process, docs on feat/sync-docs |
| Website | Static `index.html` with inline script, Google Fonts (Familjen Grotesk, Geist, Geist Mono), GitHub Pages at `utkanuygur.github.io/forge-site` | Public marketing page, no downloads | Zero hosting cost | Not recorded in repo | separate repo `forge-site` | live (separate repo) |

### Testing, CI, dev tooling

| Layer | Technology (exact version) | What it does in Forge | Why chosen | Alternatives considered | Where in the repo | Status |
|---|---|---|---|---|---|---|
| Python tests | pytest 9.1.1, pytest-asyncio 1.4.0 (`asyncio_mode = auto`); markers `docker`, `e2e`, `live` | Layers 1 to 3 and 6 | Standard | Not recorded in repo | `tests/`, `pyproject.toml` | master |
| Parallel tests | No pytest-xdist or pytest-testmon on any branch; feat/parallel-tests only fixes an index mtime race in `LocalExecutor` | None yet | n/a | xdist, testmon (not added) | `forge/executors.py` on the branch | pending: feat/parallel-tests (fix only) |
| Web unit | Vitest 2.1.9, jsdom 25.0.1, Testing Library (react 16.1.0, dom 10.4.0, jest-dom 6.6.3, user-event 14.5.2) | Layer 4a: components and hooks | Shares Vite config | Jest (not used) | `web/vitest.config.ts`, `web/tests/unit/` | master |
| Browser e2e | @playwright/test 1.62.1, Chromium; `webServer` starts the real uvicorn server with `fake:hello` and `fake:grader` on port 8011 | Layer 4b: real backend flows | Real browser, real server | Not recorded in repo | `web/playwright.config.ts`, `web/tests/e2e/` | master |
| Demo verifiers | Own scripts: checkers fail on seed and naive fix, pass on reference (21 and 50 expectations) | Layers 5 and 5b | Proves eval cases mean something | Not recorded in repo | `demo/verify.py`, `demo/verify_examples.py` | master |
| CI | GitHub Actions, one `test` job on ubuntu-latest, 30 min: checkout@v4, setup-uv@v6 (3.12, cache), setup-node@v4 (22), `uv sync --locked`, `npm ci`, sandbox build, `pytest -m "not live"`, Vitest, Playwright, `demo/verify.py`, report upload on failure | Layers 1 to 5 on push to master and every PR | One job is simple | Split jobs (feat/ci-split) | `.github/workflows/ci.yml` | master |
| CI split | 9 jobs (lint, python-unit, web-unit, sandbox, full-stack, browser, demo-checks incl. 5b, desktop-changes, desktop-compat on macos-14); composite action `sandbox-image` with Buildx and GHA layer cache | Parallel checks | Faster feedback | Single job (master) | `.github/workflows/ci.yml`, `.github/actions/sandbox-image/` on the branch | pending: feat/ci-split |
| Dev tooling | Make targets (`setup`, `test-*`, `lint`, `preflight`, `rehearse`, `serve`, `dev`, `examples`), `scripts/preflight.sh`, `scripts/rehearse.py`; lint is `compileall` plus `tsc -b` (no ruff or eslint in the repo) | Daily workflow | Small and explicit | Not recorded in repo | `Makefile`, `scripts/` | master |

## 3. What we wrote vs what we pulled in

**Written in this repo**

1. Agent loop, tool specs, truncation, redaction, turn and time limits (`forge/loop.py`).
2. Docker and local executors, git baseline diff, capped output reader, process group kill (`forge/executors.py`).
3. Provider adapters over both SDKs, retry policy, prompt cache breakpoints, stop reason mapping, `<think>` stripping, scripted fake models (`forge/providers.py`).
4. SQLite access layer and additive migration (`forge/db.py`, `forge/schema.sql`).
5. Auth model: hashed agent keys, share tokens, localhost builder with proxy header detection (`forge/server/auth.py`).
6. Run manager, SSE event stream, callback webhooks (`forge/server/runs.py`, `app.py`).
7. Eval runner, majority of k, judge, optimizer, leak check, promotion rule, pruning, provenance, drafting.
8. Observability: metrics SQL, pricing, timelines, Prometheus text format, JSON logs.
9. Web UI components, charts, live trace hook, all pages.
10. CLI commands and offline outbox; on branches the ANSI transcript renderer and `forge chat`.
11. Desktop main process, Docker checks, server supervisor, tunnel wrapper, macOS compatibility checker, headless verifier.
12. Test harness: `ScenarioProvider`, e2e conftest, demo and example verifiers.
13. Pending: approval policy engine and budgets, tool registry, HTTP tool SSRF guard, MCP connection manager, callable agents, cron parser (Python and TypeScript), webhook HMAC verification, scheduler, assist registry, settings registry, workspace tar browser, dependency free highlighter.

**Pulled in**

1. Python runtime: fastapi, starlette, uvicorn (uvloop, httptools, watchfiles, websockets), sse-starlette, pydantic, python-multipart, python-dotenv, httpx, typer (click, rich).
2. Model SDKs: anthropic, openai.
3. Pending Python: mcp (+ mcp-types, pyjwt, cryptography), jsonschema.
4. Web: react, react-dom, react-router-dom, lucide-react; pending react-markdown, remark-gfm, rehype-highlight, lowlight (highlight.js), hast-util-to-jsx-runtime.
5. Web build and test: vite, @vitejs/plugin-react, typescript, tailwindcss, postcss, autoprefixer, vitest, jsdom, Testing Library, @playwright/test.
6. Desktop: electron, electron-builder, playwright-core, PyInstaller.
7. Infrastructure: Docker, `python:3.12-slim` base image and its apt packages, vLLM, Modal, Ollama, cloudflared, GitHub Actions (checkout, setup-uv, setup-node, upload-artifact; pending setup-buildx, build-push), GitHub Pages.

## 4. Dependency footprint

| Item | master | With pending branches |
|---|---|---|
| Direct Python runtime deps | 10 | 12 (feat/tools adds `mcp>=2.3.0`, `jsonschema>=4.20`) |
| Direct Python dev deps | 2 (pytest, pytest-asyncio) | 2 |
| Packages in `uv.lock` (incl. `forge`) | 47 | 59 (feat/tools, integrate/batch) |
| Direct npm runtime deps (web) | 4 | 9 (feat/run-view) |
| Direct npm dev deps (web) | 16 | 16 |
| Packages in `web/package-lock.json` | 305 (9 non dev) | 408 (119 non dev) on feat/run-view |
| Desktop npm deps | 0 runtime, 3 dev (all exact pins) | same |
| Packages in `desktop/package-lock.json` | 284 | same |

Notable heavy dependencies: Electron 44.4.4 (bundled Chromium and Node), Playwright browsers (CI downloads Chromium), the Docker sandbox image (Debian slim plus Node, npm and Python tooling), vLLM on the GPU image, cryptography 50.0.2 (compiled, pulled in by mcp), uvloop and pydantic-core (compiled).

Licenses. `uv.lock` carries no license metadata, so Python licenses below are from the installed packages' `METADATA` files (`.venv` of master and of an integrate checkout); npm licenses are from `package-lock.json`.

| Package | License | Source |
|---|---|---|
| fastapi 0.143.0, pydantic 2.14.0, typer 0.27.3, anthropic 1.13.0, pytest 9.1.1, jsonschema 4.26.0, mcp 2.3.0 | MIT | installed METADATA |
| starlette 1.7.0, uvicorn 0.54.0, sse-starlette 3.5.0, httpx 0.28.1, python-dotenv 1.2.4 | BSD-3-Clause | installed METADATA |
| openai 3.28.0, python-multipart 0.0.32, pytest-asyncio 1.4.0 | Apache-2.0 | installed METADATA |
| cryptography 50.0.2 | Apache-2.0 OR BSD-3-Clause | installed METADATA |
| Other Python transitive packages | unknown (not in lockfile metadata, not checked) | none |
| react, react-dom, react-router-dom, vite, vitest, tailwindcss, jsdom, Testing Library, react-markdown, remark-gfm, rehype-highlight, lowlight | MIT | package-lock.json |
| lucide-react 0.460.0 | ISC | package-lock.json |
| typescript 5.6.3, @playwright/test 1.62.1, playwright-core 1.62.1 | Apache-2.0 | package-lock.json |
| highlight.js 11.11.2 | BSD-3-Clause | package-lock.json (feat/run-view) |
| electron 44.4.4, electron-builder 26.16.1 | MIT | desktop/package-lock.json |
| vLLM 0.10.2, PyInstaller, cloudflared, Qwen3 Coder weights | unknown (not in any lockfile) | none |
| Forge desktop package | UNLICENSED (private) | desktop/package.json |

## 5. Security relevant choices

| Area | Choice | Where | Status | Known gap |
|---|---|---|---|---|
| Sandbox isolation | One container per run, non root `agent` user, 1 CPU, 1 GB, 256 pids, in container kill timeout | `forge/executors.py`, `sandbox/Dockerfile` | master | Shared kernel (Docker, not a VM); no seccomp or capability changes beyond defaults |
| Network | `--network none` unless the version enables network, then full `bridge` egress | `forge/executors.py` | master | No egress allowlist |
| Model keys | Provider keys live only in the server process; Docker runs get only the agent's own secrets | `forge/providers.py`, `forge/server/runs.py` | master | CLI `LocalExecutor` runs bash with the host environment, so host keys are visible to local runs |
| Agent secrets | Stored in the SQLite `secrets` table in plain text; injected as container env; values redacted from events and output (longest first) | `forge/db.py`, `forge/loop.py` | master | Not encrypted at rest; visible to anyone with the DB file or `docker inspect` |
| Desktop secrets | Claude key encrypted with Electron `safeStorage` (Keychain); renderers use `contextIsolation`, `sandbox`, no Node integration | `desktop/src/settings.js`, `desktop/src/main.js` | master | App not notarized |
| Auth model | Builder routes only from localhost; runtime routes need `Bearer fk_...` (SHA-256 hashed, constant time compare) or a share token; any proxy header (`CF-Connecting-IP`, `CF-Ray`, `X-Forwarded-For`, `X-Real-IP`, `Forwarded`) makes a request remote | `forge/server/auth.py` | master | No users, roles or rate limits on the API; `FORGE_TRUST_ALL=1` disables checks |
| Share token scope | Agent view limited to id, slug, name, description, suggested prompts; run list filtered to share runs; no config, versions, evals or improvements | `forge/server/app.py` | master | A share token can read any run of its agent by ID |
| Share token, pending | Workspace files only for `share` runs; approvals POST refused; assist rate limited (10 per hour) | branch files | pending: feat/playground-extras, feat/governance, feat/generate-task | none beyond the above |
| SSRF | HTTP tools: only http and https, every resolved IP must be public, connection pinned to the checked IP, redirects rechecked per hop, headers dropped across origins; `FORGE_ALLOW_PRIVATE_HTTP=1` disables | `forge/tools/http.py` | pending: feat/tools | Remote MCP checked but not pinned (DNS rebinding); `callback_url` webhooks have no SSRF check on master or integrate/batch |
| Webhook signatures in | GitHub HMAC SHA256 over the raw body; Slack `v0` with 5 min timestamp window; constant time compare; bot events ignored; 5 MB cap; Slack replies only to `https://hooks.slack.com/` prefixes | `forge/triggers/security.py`, `slack.py` | pending: feat/triggers | Trigger secrets stored like other secrets (plain text) |
| Webhooks out | `callback_url` gets the final Run JSON, 3 retries | `forge/server/runs.py` | master | Unsigned, so receivers cannot verify the sender |
| Human approval | `ask` and `deny` rules on tool calls, 15 min auto deny | `forge/policy.py` | pending: feat/governance | Rules match patterns, not intent |
| Workspace browsing | Archive relative paths only, no `..`, symlinks resolved inside the archive, `.git` hidden | `forge/server/files.py` | pending: feat/playground-extras | None known |
| Tunnel | cloudflared requests count as remote, so the builder UI is never exposed | `forge/server/auth.py`, `desktop/src/tunnel.js` | master | Quick tunnel URL is public to anyone who has it |

## 6. Known limits and what changes at scale

| Limit today | Why it is fine now | What changes at scale |
|---|---|---|
| One process, in memory task registry and semaphore (8 runs, 4 eval runs) | Single machine target (NFR7) | Separate API from workers with a durable queue |
| SQLite file, one connection per call | WAL handles a laptop's write rate | Postgres with pooled async access; real migrations |
| Artifacts on local disk | One user | Object storage with signed URLs |
| Docker on the host, shared kernel | Trusted builders | gVisor or Firecracker microVMs, remote executor fleet |
| Network on or off only | Simple policy | Egress allowlists and a proxy |
| Secrets in plain SQLite | Local only | Encryption at rest or a secrets manager |
| No user identity, localhost builder | Single author | Accounts, roles, audit log, API rate limits |
| Evals at k=3 by majority, no intervals | Small suites | Confidence intervals, judge calibration, parallel eval workers |
| Prompt cache tokens not priced | Cost is an estimate | Price cache reads and writes |
| Sandbox image pip packages unpinned | Rebuilt rarely | Pin and lock the image |
| Desktop: arm64 macOS only, unsigned, no auto update | Private distribution | Developer ID, notarization, updater, Intel or universal build |
| GPU model scales to zero after 15 min | Cheap | `min_containers` above 0 or a dedicated deployment to avoid cold starts |

## 7. Drift found between branches and docs

| Item | Detail |
|---|---|
| CI | Master runs one job; `.github/actions/` exists only on feat/ci-split, which is not merged |
| Node version | README says Node 20+, desktop README and CI use 22 |
| App version | `desktop/package.json` and `pyproject.toml` say 0.1.0; v0.1.1 and v0.1.2 are published |
| Providers merge | feat/run-view branched before prompt caching and stop reasons; its `providers.py` lacks both, so the merge needs care |
| Highlighters | feat/run-view uses lowlight (highlight.js); feat/playground-extras adds its own dependency free highlighter |
| Terminal rendering | `forge/cli_render.py` (feat/playground-extras) notes feat/run-view may bring its own renderer |
| Price table | feat/governance has a fallback price table whose `FORGE_PRICES` object keys are `in`/`out`, while `forge/pricing.py` uses `input`/`output`; integrate/batch already resolves to `forge.pricing` only |
| Parallel tests | feat/parallel-tests adds no xdist or testmon, only a `LocalExecutor` mtime fix (also on feat/sync-docs) |
| TESTING.md on master | Says "six layers" and FR15 untested; feat/tools and feat/sync-docs update both |
