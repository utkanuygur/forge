# TDD updates: what to change in "Forge: Technical Design Document"

Report only. The TDD is a Claude Doc the lead edits; nothing here has been applied to it. Section numbers and
headings below are the ones in the current TDD (revision 25, read 2026-10-10): 1 Overview through 15 Future work,
with 6.1 to 6.8 in Detailed design. Note that section 12 is titled "Alternatives considered" (the decision log
table D1 to D15); additions to the decision log go there.

Each item says where, then the exact text to add or the replacement. Items tagged **[master]** describe what is
on master today. Items tagged **[pending: branch]** describe work on a branch that is not merged yet: add them
only once that branch merges (see docs/pending-sync.md), or add them now under a "Planned" marker.

Released app builds: v0.1.1 (core, observability, repeated evals, drafting, pruning) and v0.1.2 (adds example
agents, suggested prompts, macOS 12 support), both on the code repository's Releases page.

---

## 0. Stale statements already in the TDD (fix these regardless)

| Section | Current text | Problem | Replace with |
|---|---|---|---|
| 1.3 Non-goals | "Billing, quotas and usage metering." | Forge now meters cost per run, version and improvement cycle; governance adds budgets | "Billing and invoicing. (Forge estimates cost per run and enforces optional per-agent budgets, but does not bill anyone.)" |
| 3.1 FR15 | "Authors can define typed HTTP tools ... P2" | On master `http_tools` is stored but never offered to the model | Keep FR15, set Priority to P1 and add "Status: [pending: feat/tools]". |
| 3.2 NFR4 | "Under 2 seconds per run" | No test measures it | Add to Target: "(not measured by any test yet)". |
| 6.1 Limits | "Evaluation runs use temperature 0 to reduce variance." | Current Claude models (Opus 4.7 / Sonnet 5 onward) reject sampling parameters, so the provider drops temperature for them; variance is handled by repeated evals | "Evaluation runs, the judge and the optimizer request temperature 0; the Anthropic adapter sends it only to models that accept sampling parameters (`accepts_temperature`), which excludes current Claude models. Variance is therefore controlled by repeating each case k times (6.6.2), not by sampling settings." |
| 6.3 table, Anthropic row | "always used for the optimizer and the judge" | The grader is configurable (`FORGE_GRADER_MODEL`, falling back to `FORGE_DEFAULT_MODEL`) | "default for agents (`anthropic:claude-sonnet-5-5`); the grader (judge, optimizer, drafting) is `FORGE_GRADER_MODEL`, set to `anthropic:claude-opus-5-5` in `.env.example`" |
| 6.3 Grader independence | "pinned to a strong hosted model regardless of the agent's own model" | True only when `FORGE_GRADER_MODEL` is set | Append: "If `FORGE_GRADER_MODEL` is unset it falls back to the agent default model; `.env.example` sets it, and the preflight should warn when it equals an agent's model." |
| 6.5 Share page row | "/s/{token}" | Actual URL is `/s/{slug}?share=<token>` | "/s/{slug}?share={token}" |
| 6.5 Inputs and outputs | "Callers may upload files or reference a Git repository." | No `git_url` support exists | "Callers may upload files (multipart, or one .tar.gz that is extracted) or pass `seed_files` as JSON." |
| 6.5 Run lifecycle | "queued, running and one terminal state: succeeded, failed or timeout" | Correct on master; pending branches add states | Leave; add pending states from sections B and E when those merge. |
| 6.8 example | `--model ollama/qwen3:8b` | Model strings are `openai_compat:<base_url>\|<model>` | `--model "openai_compat:http://localhost:11434/v1\|qwen3:8b"` |
| 6.8 | (no login step) | The CLI needs `forge login <url> <agent-api-key>` first | Add "forge login http://localhost:8000 fk_..." as the first example line. |
| 7 runs table | columns list | Missing columns added since | Add `cache_read_tokens, cache_write_tokens, error, created_at` to runs; `lesson_meta` to agent_versions; `eval_repeats, suggested_prompts` to agents; `passes, runs` to eval_results; new tables `eval_run_results`, `improvements` (with `kind`), `promotions`, `grader_calls`, `improvement_runs`. |
| 7 agent_versions note | "Immutable; never updated after insert" | `lesson_meta` is written after insert | "Configuration is immutable; only `lesson_meta` (lesson provenance) is written after insert." |
| 8.2 POST runs | "files, git_url, wait, callback_url" | No git_url; JSON body also accepted | "JSON `{input, version?, wait?, callback_url?, seed_files?}` or multipart `input, version?, wait?, callback_url?, files`" |
| 8.3 | `GET /agents/{slug}/compare?a={n}&b={n}` | Does not exist | Replace with `POST /agents/{slug}/evals/run`, `GET /agents/{slug}/evals/results?version=n`, `GET /improvements/{id}`, `GET /agents/{slug}/improvements`, `POST /agents/{slug}/prune`, `DELETE /evals/{id}`. |
| 8.5 Example | empty heading | Section has no body | Add a curl example: `curl -X POST $URL/v1/agents/repo-maintainer/runs -H "Authorization: Bearer fk_..." -F input="add a refund endpoint" -F wait=true` and the abbreviated Run JSON from CONTRACT.md. |
| 8 intro | "Runtime endpoints accept either the agent's API key or its share token." | Missing the localhost builder rule and proxy header handling | Append: "Requests from localhost without credentials act as the builder; any proxy header (CF-Connecting-IP, CF-Ray, X-Forwarded-For, X-Real-IP, Forwarded) makes a request remote, so tunnel traffic never gets the builder bypass." |
| 9 Unauthorized API use | Residual risk "No per-user identity or rate limiting" | Still true; also a share token can read any run of its agent by id | Append: "A share token can read any run of its agent by run ID (only the run list is filtered to share runs)." |
| 10.1 Docker daemon unavailable | "A configuration flag routes hosted runs to the local executor" | Flag is `FORGE_EXECUTOR=local`; `/healthz` reports Docker as skipped | Name the flag: "`FORGE_EXECUTOR=local` ..." |
| 13.1 | "A typed HTTP tool is called with arguments that match its schema / FR15" | No such test on master | Mark "[pending: feat/tools]". |
| 13.3 | "Stopping the Docker daemon triggers the documented local executor fallback" | No automatic fallback exists; it is a manual flag, and `/healthz` returns 503 when Docker is down | "Stopping the Docker daemon makes `/healthz` return 503 with the docker component in error; `FORGE_EXECUTOR=local` is the manual fallback." |
| 14 Milestones | "the event schema and API contract are frozen after Milestone 1" | The contract has grown by additive amendments | Append: "After the freeze, every change was an additive amendment recorded in CONTRACT.md (new optional fields, endpoints and event payload keys), so earlier clients and stored traces stay valid." |

---

## A. Observability **[master]**

- **10.2 Observability**: replace the four bullets with:
  - "Traces remain the primary debugging tool. Events carry additive timing and usage fields: `assistant` has `turn`, `duration_ms`, `tokens_in`, `tokens_out`; a tool-only turn reports them on its first `tool_call` as `model_duration_ms`; `tool_result` has `duration_ms`."
  - "Per run: duration, turns, tool calls, tokens (including prompt cache reads and writes), cost (`forge/pricing.py`, USD per million tokens, overridable with `FORGE_PRICES`; self-hosted models are free and labeled), and a step timeline (`GET /runs/{id}/timeline`) built from recorded durations, or estimated from timestamps for old runs."
  - "Per version and agent: success rate, p50 and p95 duration, average turns, thumbs up rate, cost per run, eval pass rates per split next to production health, promotions, failure breakdown (timeout, model_error, sandbox_error, tool_errors, eval_failed, negative_feedback) with example runs, and per tool call counts, error rates and latency. Improvement cycles are costed (eval runs plus judge and optimizer calls, `grader_calls`)."
  - "Dashboard: `GET /metrics/overview` (all agents, KPIs with deltas against the previous period, local-day buckets) and per agent Monitoring (`GET /agents/{slug}/metrics`). Run search and filters over status, version, source, rating, tool errors, text and time, with `X-Total-Count`."
  - "Ops: `GET /healthz` (database and Docker image, 503 when a component fails), Prometheus `GET /metrics` (runs, durations, tokens, cost, tool calls, active runs, improvements; localhost unless `FORGE_METRICS_PUBLIC=1`), and JSON lifecycle logs (`forge.lifecycle`, `FORGE_LOG_JSON`)."
  - "Everything is computed from Forge's own database; nothing is sent to third parties."
- **6.7 Web application**: add rows "Dashboard | See every agent's health | KPIs with period deltas, runs and success chart, top failing agents and failure reasons" and "Monitoring (agent tab) | Compare versions | Version health next to eval pass rates, cost per run and per cycle, failure breakdown, tool statistics, slowest runs, promotion markers".
- **7 Data model**: add tables `promotions` (every prod pointer change: create, human, auto), `grader_calls` (judge and optimizer usage, with `improvement_id` and `run_id`), `improvement_runs`; index on `runs(created_at)`; migrations through `forge.db.ADDED_COLUMNS`.
- **8**: add "8.6 Observability endpoints" with the routes above.
- **11**: row 5 (Evaluation cost) remedy: append "Cycle cost is now measured per improvement, so the remedy can be targeted."
- **15.1**: the "Observability export" item stays future work; prefix it with "Built in observability shipped (Section 10.2);".

## B. Repeated evals, majority of k **[master]**

- **6.6.2**, after "Evaluation results are cached per version and case": "Each case runs k times per version (agent setting `eval_repeats`, 1 to 9; default `FORGE_EVAL_REPEATS` or 3). A case passes for a version only if a strict majority of its k runs pass (for even k a tie fails). Per-run verdicts are stored in `eval_run_results`; `eval_results` holds the per-case summary (`passes`, `runs`, a representative run: the first failing run when the case failed). Raising k reuses stored runs and adds only the missing ones; a run whose checker never ran is not stored and is retried. At most 4 eval sandbox runs execute at once across all cases and repeats."
- **6.6.4 Promotion rule**: after the two conditions add "'Passes' means the per-case majority verdict."
- Replace "Cases run four at a time" with "At most four eval runs execute at once (all cases and repeats share the bound)."
- **11** row 6 (Single evaluation per case): mark done: "Done: each case runs k times and passes by strict majority. Confidence intervals remain future work."
- **12**: add **D16** "Majority of k per case | A single run flips with model nondeterminism; majority is simple to explain and keeps the promotion rule unchanged | Confidence intervals or pass@k | Suites large enough for statistical thresholds".
- **15.1** "Evaluation rigor": change to "Judge calibration against human labels, and confidence intervals over repeated runs (repeated runs with a majority verdict shipped)."

## C. Prompt caching and stop reasons **[master]**

- **6.3**, new paragraph "Stop reasons": "Providers return a normalized `stop_reason` (`end_turn`, `tool_use`, `max_tokens`, `refusal`, ...; OpenAI `finish_reason` is mapped: `length` to `max_tokens`, `content_filter` to `refusal`). A refusal fails the run with `Model declined: <category>` and none of that turn's tool calls run. A `max_tokens` reply without tool calls fails the run (a truncated answer is never accepted as final); with tool calls, all but the last call run and the last gets an error result because its arguments may be cut off. `max_tokens` is 16,000 because thinking is always on for current Claude models."
- **6.3**, new paragraph "Prompt caching": "The Anthropic adapter marks the system block (which caches tools plus system) and, in tool-using calls, the last message block (which caches the growing conversation) with ephemeral `cache_control`, 2 of the 4 breakpoints. Cache read and write tokens are recorded per turn and per run; `tokens_in` stays the uncached count. Cache tokens are reported but not yet priced."
- **10.1** add rows: "Model refuses | Run fails with `Model declined: <reason>`; no tool call from that turn runs" and "Reply cut off at max_tokens | No tool calls: run fails; with tool calls: the last call is returned as an error and the model retries".
- **12**: add **D17** "Cache tools, system and conversation | Agent turns resend a growing prefix; caching cuts latency and input cost | No caching | Providers price cache writes above their benefit for short runs".

## D. Agent drafting **[master]**

- **6.4**, after Templates: "Describe your agent. `POST /agent-drafts` sends a plain description (optional examples and constraints) to the grader model once, with structured output (JSON schema) for Claude and lenient parsing elsewhere. The draft (name, free slug, system prompt of 300 to 8000 characters, built-in tools, network with a reason, suggested secret names, a seed files hint and 3 to 6 starter eval cases with at least one held-out case) is validated and retried once with the problems listed; an invalid second reply is a 502. Nothing is stored: the author reviews and edits it, selects cases, and `POST /agents` creates the agent and those cases together."
- **6.7**: Agents screen key elements: "New agent: describe it, blank, or template".
- **8.1**: add `POST /agent-drafts` and note that `POST /agents` accepts `eval_cases`.
- **12**: add **D18** "Draft, then review | Speeds up a blank page without creating anything unreviewed | Creating the agent directly from the description | Drafts are accepted unedited almost always".

## E. Lesson pruning and provenance **[master]**

- **6.6**, new subsection "6.6.5 Lesson pruning": "`POST /agents/{slug}/prune` evaluates production, then ablates up to `max_ablations` (default 8) lessons oldest first on training cases: each ablation is production minus one lesson. A lesson is unhelpful when removing it does not reduce training passes, helpful when it does, unknown when the ablation could not be fully evaluated. One candidate removes every unhelpful lesson and is evaluated on both splits. Pruning rule: eligible if there are zero regressions and the candidate passes at least as many cases as the base (a tie is enough, since the goal is a smaller prompt at equal quality). Prune candidates are never auto-promoted. Cached results of identical configurations are reused. Prune jobs do not reset the negative feedback window."
- **6.6**, new subsection "6.6.6 Lesson provenance": "Each version carries `lesson_meta`: per lesson, the version that introduced it, its evidence run IDs, its origin (optimizer or human), the edit it came from, and any pruning verdict. The Configure tab shows each lesson's origin and evidence."
- **11** row 7 (Lessons accumulate): mark done: "Done: leave-one-out pruning retires lessons that never helped. Consolidation (merging overlapping lessons) remains future work."
- **12**: add **D19** "Leave-one-out pruning with a tie-accepting rule | Shrinks prompts without trusting a model to judge its own lessons | Optimizer-proposed removals | Ablation cost outgrows its benefit (many lessons, large suites)".

## F. Approvals and budgets **[pending: feat/governance, in integrate/batch]**

- **6.5**, new paragraph "Governance": "Each version can carry an approval policy (rules on `bash` command regexes or file path globs, mode `ask` or `deny`, timeout default 15 minutes) and a per-run cost cap; each agent can carry a monthly budget. An `ask` match pauses the run in `awaiting_approval` (the sandbox stays alive) until someone approves or denies it in the UI or API; a deny or timeout returns `Denied by reviewer` to the model. A run over its cap ends `budget_exceeded`. At or over the monthly budget, new runs, evals and improvements return 402. Warnings fire at 80%."
- Run lifecycle (6.5): add states `awaiting_approval` (non-terminal) and `budget_exceeded` (terminal).
- **7**: `approvals` table; columns `agents.monthly_budget_usd`, `agent_versions.approval_policy`, `agent_versions.max_run_cost_usd`, `runs.cost_usd`.
- **8**: `GET /runs/{id}/approvals`, `POST /runs/{id}/approvals/{approval_id}`, `GET /agents/{slug}/governance`.
- **9**: new threat row "Agent takes a destructive or costly action | Approval rules pause matching tool calls for a human; deny rules block them; per-run and monthly spending caps | Rules match patterns, not intent; costs are estimates from token prices".
- **12**: add **D20** "Human approval on matched tool calls | Lets risky agents be shared without full trust | Blocking all risky tools | Policies grow too large to maintain by hand".

## G. Tools: HTTP, MCP, agents calling agents **[pending: feat/tools, in integrate/batch]**

- **6.1**, after Built-in tools: "Extra tools come through one registry seam (`run_agent(..., extra_tools=...)`): typed HTTP tools (JSON schema, URL templating, secrets as `{{NAME}}` in headers, run in the server process, need network on), MCP servers (HTTP, SSE or stdio; stdio servers run inside the run's sandbox; tools named `mcp__<server>__<tool>`) and callable agents (`agent__<slug>`, a child run of the callee's production version, at most 3 levels, no cycles, child usage rolled up into `child_usage`)."
- **9**: new row "Server-side request forgery through HTTP tools or MCP | Only http and https; every resolved address must be public; HTTP tool connections pinned to the checked IP, redirects rechecked per hop, headers dropped across origins | Remote MCP connections are checked but not pinned (DNS rebinding)".
- **3.1 FR15**: Status done; add FR16 "Agents can use tools from MCP servers" and FR17 "An agent can call another agent as a tool".
- **13.1**: the FR15 row becomes real; add rows for MCP and callable agents.
- **15.1** "Tool ecosystem": move to done.
- **12**: add **D21** "MCP via the official SDK, stdio inside the sandbox | Reuses the ecosystem without giving servers host access | Custom adapters per service | MCP SDK lacks a needed transport".

## H. Triggers **[pending: feat/triggers]**

- **6.5**: add a fourth surface row "Triggers | Schedules and external events | Cron (with time zone), GitHub webhooks (HMAC SHA256 signed), Slack slash commands and events (v0 signature, 5 minute window)". Paragraph: "A trigger starts runs of one agent from a templated input; runs carry source `schedule`, `github` or `slack`. GitHub and Slack replies go back as comments or threaded messages, best effort with retries. A scheduler loop fires each slot at most once (the slot is advanced before the run starts), skips slots more than 10 minutes late and can skip while the previous run is still going. Per-trigger rate limit (default 30 per hour)."
- **7**: tables `triggers`, `trigger_fires`.
- **8**: trigger builder routes and `POST /hooks/github/{id}`, `POST /hooks/slack/{id}`.
- **9**: new row "Forged webhook calls | Signature verification per trigger, Slack timestamp window, bot events ignored, rate limits, 5 MB cap | Secrets stored like other secrets".
- **14** Milestone 7 "Extensions": "auto-promotion triggers" is ambiguous; rename to "auto-promotion (shipped), triggers (schedules, GitHub, Slack)".

## I. Claude Code style transcript, Playground sessions and cancel **[pending: feat/run-view]**

- **6.1** Tracing: add "Narration: the model's short progress updates between tool calls (Claude progress-update thinking summaries) are emitted as `assistant` events with `kind: \"progress\"`; the final answer has `kind: \"final\"`. Agents are instructed to narrate and to edit files with the file tools."
- **6.7**: Run screen key elements become "Claude Code style transcript (narration, commands with output, file edits as diffs), timeline view, diff, artifacts, rating, Save as test case". Add a screen "Playground | Iterate on an agent | Multi-turn sessions where each turn continues from the previous turn's workspace, draft sessions that try unsaved config without creating a version, Stop".
- **6.5** Run lifecycle: add terminal state `cancelled` (`POST /runs/{id}/cancel`, error event `Stopped by user`, workspace still exported).
- **7**: `sessions` table; runs gain `session_id`, `turn_index`; source `playground`.
- **8**: session routes (`POST /agents/{slug}/sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/messages`, `POST /runs/{id}/cancel`).
- **6.7** "Why five screens": update the count.

## J. Workspace files and forge chat **[pending: feat/playground-extras]**

- **6.5** Inputs and outputs: add "Callers can browse a finished run's workspace file by file (`GET /runs/{id}/files`, `GET /runs/{id}/files/{path}`), read only, from the exported archive; `.git` and links leaving the archive are never served; text files only, up to 1 MB."
- **6.8**: add "`forge chat <slug>[@vN]` opens a terminal REPL over Playground sessions: each line is a turn, the transcript renders live, Ctrl+C cancels the turn, and `/files` and `/open` browse the latest workspace."
- **9**: new row "Path traversal in workspace browsing | Archive-relative paths only, no `..`, no absolute paths, symlinks resolved inside the archive only | None known".

## K. Desktop app and release process **[master]**

- **5 System architecture**: add "Packaging: the same server also ships as a macOS app (Electron shell, PyInstaller-frozen server bound to 127.0.0.1, bundled web app). The app checks Docker Desktop, builds the sandbox image on first launch, keeps the Claude API key encrypted with Electron `safeStorage` (macOS Keychain) and can open a Cloudflare quick tunnel for public links."
- **6**, new subsection **6.9 Desktop app**: requirements (Apple Silicon, macOS 12 or later, Docker Desktop); server built with uv's prebuilt CPython so it runs on macOS 12; `check-min-macos.sh` fails the build if any bundled binary needs a newer macOS or links outside the bundle; not notarized (Open Anyway or `xattr`); headless verification with Playwright's Electron support (`npm run verify`).
- **14**: add Milestone 8 "Desktop app and releases. Exit criterion: a dmg installed on a clean Mac runs an agent in Docker; every bundled binary targets macOS 12."
- New paragraph in **14** "Release process": "Each release is a version tag on the private code repository with the dmg, notes (what is new, requirements, install, what was verified, SHA-256). v0.1.1 and v0.1.2 shipped on 2026-10-10. The public website does not host downloads; the app and source are available on request."
- **12**: add **D22** "Electron app with a frozen Python server | One download for non-developers, same server code | Native Swift shell, or asking users to install uv and Node | Windows or Linux demand".

## L. Example agents **[master]**

- **13.2**: add "Example agents: log triage, data analyst and docs writer join repo maintainer, each with seed files built deterministically, planted ground truth, suggested prompts and verified eval cases. `demo/verify_examples.py` proves every shell checker fails on the seed, fails on a plausible wrong answer where one exists, and passes on the reference solution (50 expectations); judge rubrics are graded against references with the real grader."
- **6.4** or **4**: add concept "Suggested prompts: starter tasks stored on the agent (not the version), shown as chips above the run input and on the share page; changing them never creates a version."
- **7**: `agents.suggested_prompts` (JSON).

## M. Docker sandbox (not OrbStack) and local Qwen via Ollama **[master]**

- **6.2**: add "The supported container runtime is Docker (Docker Desktop on macOS, Docker Engine on Linux); the desktop app checks for Docker Desktop specifically and the tests run against Docker. Other Docker-compatible runtimes are untested."
- **6.3 Local models**: make the reference model concrete: "The documented local model is `qwen3:8b` on Ollama (`ollama pull qwen3:8b`), model string `openai_compat:http://localhost:11434/v1|qwen3:8b`, which is one of the web app's model presets and the default of the live local model test."
- **6.3 GPU**: name the deployment: "`gpu/vllm_modal.py` serves Qwen/Qwen3-Coder-30B-A3B-Instruct as `qwen3-coder` with the `qwen3_coder` tool-call parser."

## N. CI **[master, changes on feat/ci-split]**

- **13**, new subsection **13.4 Continuous integration**: "[master] One GitHub Actions job runs on every pull request and every push to master: layers 1 to 3 (`pytest -m \"not live\"`), web unit and browser tests, and the demo checkers, with the sandbox image built fresh. Live model tests never run in CI." When feat/ci-split merges replace with: "Each layer is its own parallel check (lint, Python unit, sandbox, full stack, web unit, browser, demo and example checks, desktop macOS 12 compatibility build on macos-14 runners, gated by path filters); one sandbox image is built with Buildx and shared."
- **13.1**: add a sentence: "The deterministic layers replace the model with scripted `fake:` scenarios while the server, database, sandboxes, CLI and browser stay real; see TESTING.md for the layer list and the requirement traceability table."

## O. Assist (Generate buttons) **[pending: feat/generate-task]**

- **6.7**: "Generate: a button next to the task box asks the grader for one realistic sample task grounded in the agent's prompt, tools, seed files and recent inputs (`POST /assist/task`, alias `POST /agents/{slug}/sample-task`). It never saves anything and never reveals secret values; share links get at most 10 per hour."

---

## Future work (15.1): items now done and where they move

| 15.1 item | Status | Move to |
|---|---|---|
| Tool ecosystem: MCP servers alongside typed HTTP tools | Done on feat/tools (not merged yet) | 6.1 and FR15 to FR17 when merged |
| Evaluation rigor: repeated runs per case with confidence intervals | Half done: repeated runs with majority verdict on master; confidence intervals and judge calibration remain | 6.6.2; keep the remainder in 15.1 |
| Observability export (LangSmith, OpenTelemetry) | Still future; built in observability shipped instead | Keep, reworded as in A |
| Access control, canary releases, few-shot optimization, remote tool daemon, egress allowlists | Not done | Keep |

Section 11 rows now addressed: row 6 (single evaluation per case, by repeated evals) and row 7 (lessons
accumulate, by pruning). New candidate rows: "Approval waits hold a concurrency slot" (pending governance) and
"Prompt cache tokens are not priced" (cost is understated for long runs).

Open questions (15.2): "Should auto-promotion require a minimum number of held-out cases" is still open; add
"Should prune candidates ever auto-promote?" and "Should share tokens be limited to reading share runs by ID?"
