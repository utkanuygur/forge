# Forge testing workflow

Forge is tested in six layers, ordered from fastest to slowest. Every deterministic layer runs without
API keys, because the model is replaced by `ScenarioProvider` (`fake:<scenario>` model strings), which
makes runs, judging and optimization reproducible while everything else (server, database, Docker
sandboxes, streaming, CLI, browser) is real.

**Why a scripted model instead of mocks everywhere:** mocks would test our assumptions about the server
and sandbox, not their behavior. A deterministic model keeps every other component real, so a passing
end-to-end test means the platform works; only the model's judgment is simulated, and that is covered
separately by the live layer.

## Layers

| # | Layer | Command | Needs | Proves | Typical time |
|---|---|---|---|---|---|
| 1 | Unit | `make test-unit` | nothing | Loop logic, tool semantics, truncation, redaction, providers' message mapping, retry, lesson validation, promotion rule, DB helpers, API handlers with fakes | seconds |
| 2 | Sandbox | `make test-docker` | Docker, `forge-sandbox` image | Network isolation, resource limits, file persistence, diff and archive export, timeouts kill processes, no leaked containers | ~10 s |
| 3 | Full stack | `make test-e2e` | Docker | Real server + Docker + fake models: run lifecycle, SSE, uploads, versions, auth, feedback, secrets, limits, webhook, local CLI, **full improvement loop including regression blocking and auto-promotion**, observability metrics, cost, search, timelines, health and Prometheus endpoints | 1 to 3 min |
| 4 | Web | `make test-web` | Docker, Chromium (Playwright) | Component and hook logic (Vitest); browser flows against the real backend: create, run, feedback, versions, evaluate, improve, promote, share page hides config, dashboard, monitoring, run filters, step timeline, phone width | 1 to 3 min |
| 5 | Demo validity | `make test-demo` | Docker | Each demo eval case FAILS on the untouched repo, FAILS on a correct but convention-violating fix, PASSES on the reference fix (21 expectations) | ~1 min |
| 5b | Example validity | `make test-examples` | Docker | Every example agent's shell checker (eval cases and suggested prompt checks) FAILS on the seed, FAILS on a plausible wrong answer where one exists, PASSES on the reference solution (50 expectations); `--judge` also grades the 4 judge cases with the real grader | ~30 s |
| 6 | Live models | `make test-live` | `ANTHROPIC_API_KEY`; optional Ollama, `FORGE_VLLM_URL` | Real Claude completes a task in the sandbox; judge gives sensible verdicts; local and GPU models emit valid tool calls | 1 to 2 min, costs tokens |

`make test` runs layers 1 to 3 (skipping Docker tests automatically if Docker is unavailable).
`make test-all` runs every deterministic layer (1 to 5). Live tests never run unless asked
(`-m live` or `FORGE_LIVE=1`).

## Fake model scenarios

| Model string | Behavior | Used for |
|---|---|---|
| `fake:hello` | Writes `hello.py`, runs it with bash, finishes | Run lifecycle, traces, diffs, CLI |
| `fake:echo` | Finishes immediately echoing the input | Fast API and UI tests |
| `fake:lessons` | Creates `MARKER.txt` only when a lesson says so | Improvement loop: v1 fails `test -f MARKER.txt`, v2 passes |
| `fake:grader` | As judge: passes. As optimizer: proposes the MARKER lesson citing real failing run IDs. With the drafting prompt (`forge.drafting.DRAFT_SYSTEM`): returns the fixed, valid `FAKE_DRAFT` ("Issue triager", network on, `GITHUB_TOKEN`, 3 train and 1 holdout case) whatever the description | Grader, optimizer and agent drafting without API calls |
| `fake:loop` | Calls bash forever | Turn limit and `timeout` status |
| `fake:error` | Raises like a failed API | `failed` status and error events |
| `fake:missing` | Raises `ModelNotFoundError`, as if the model does not exist on its provider | Evals stop early instead of running k times per case |
| `fake:flaky` | Alternates per run: the 1st, 3rd, 5th, ... run with a given (system prompt, input) writes `FLAKY.txt`, the others do not. A process-wide counter keyed by sha256(system, input) advances on each run's first model call, so k concurrent runs of one case yield ceil(k/2) passes of `test -f FLAKY.txt` in any order (k=3: 2/3 majority pass; k=2: 1/2 tie fails). Server restart resets it; give each test its own system prompt or input | Repeated evals and the majority rule end to end |

Cost assertions are deterministic because the end-to-end server (`tests/e2e/conftest.py`) and the browser test
server (`web/playwright.config.ts`) set `FORGE_PRICES` to give the fake models a price; scripted grader responses
report fixed token counts.

## Workflow

| When | Run | Gate |
|---|---|---|
| While coding | `make test-unit` (or `uv run pytest tests/test_<area>.py`) | Must pass |
| Before each commit | `make test` | Must pass |
| Before merging or demoing | `make test-all` | Must pass |
| After changing prompts, the optimizer or the demo | `make test-demo`, then `make rehearse` | Rehearsal must print `OK: held-out improved` |
| Before a live demo | `make serve` in one terminal, `make preflight` in another | Every line PASS (WARN only for optional GPU and local model) |

`make rehearse` runs the real improvement loop on the demo agent with Claude: it evaluates v1, runs one
improvement cycle and prints per-split scores, the learned lessons and a verdict. The demo is credible
only if held-out cases improve with zero regressions; the script explains how to tune the demo when not.

## Requirement traceability

Requirement IDs are defined in the technical design document. Each row names the tests that prove it.

| Requirement | Layer: test |
|---|---|
| FR1 Create agents | 3: agent creation via API; 4: create from blank form and from template |
| FR2 Code and file tools | 1: tool semantics (`edit_file` missing and duplicate matches); 3: run lifecycle writes and runs `hello.py`; 6: Claude task in sandbox |
| FR3 Network policy and secrets | 2: network none blocks egress, bridge allows; 3: secrets injected as env |
| FR4 Immutable versions, prod pointer | 1: new version copies prod fields; 3: v1 unchanged after v2, pin, promote, rollback; 4: version history and Promote |
| FR5 Share page and API | 1 and 3: API key, share-token scope (share view is exactly id, slug, name, description, suggested_prompts), 401 cases; 4: share page shows suggested prompt chips, runs and hides configuration |
| FR6 File inputs and artifacts | 3: JSON seed files, multipart file and tarball uploads, diff and archive contents; 4: diff view |
| FR7 Complete traces | 3: gapless ordered events ending in `run_finished`; SSE resume with `after` |
| FR8 Feedback from every surface | 3: API and CLI feedback stored against run and version; 4: thumbs down persists |
| FR9 Eval cases with splits | 3 and 4: eval run reports per-split scores; 5: checkers are valid |
| FR10 Improve and promotion rule | 1: `is_eligible` (equal score not eligible, regression blocks); 3: full loop v1 to v2 eligible, regression rejected, auto-promote promotes; 4: improve and promote in the UI |
| FR9/FR10 Repeated evals (majority of k) | 1: `majority` (odd k has no ties, even k needs more than half), representative run, `compare` carries counts, schema migration backfill, `eval_repeats` env/PATCH and run reuse (`tests/test_reliability.py`, `tests/test_server.py`); 3: `fake:flaky` k=3 passes 2/3 with 6 runs and re-evaluation is cached, k=2 tie fails, improvement compares majority verdicts (`tests/e2e/test_repeats.py`); 4: "0/3" and "3/3" cells, Eval repeats setting |
| FR11 Save run as eval case | 3: save-as-eval; 4: saved case appears in Evaluations |
| FR12 Model choice | 1: provider message mapping and retries; 6: Claude, local model, GPU model |
| FR12 Model choice: stop reasons and prompt caching | 1: refusal fails with `Model declined: <category>` and runs no tool calls, max_tokens without tool calls fails, truncated last call is not executed, OpenAI finish_reason mapping, `cache_control` on system and newest message block, cache usage parsed and summed into `run_finished` (`tests/test_reliability.py`); 6: second and later Claude turns report cache reads |
| FR13 Local runner | 1: path confinement, ask mode; 3: `forge run` creates files locally and syncs a `local` run that never affects eval results |
| FR14 Webhook | 3: receiver gets the final Run JSON |
| FR15 HTTP tools | Not implemented (P2); no test |
| NFR1 Isolation | 2: network none, non-root user, limits; 3: no leaked containers after the suite |
| NFR2 Reproducibility | 3: every run and eval result records its version |
| NFR3 Live trace | 3: SSE delivers events before the run ends; 4: trace renders live |
| NFR5 Bounded execution | 1: turn and wall-clock limits; 2: command timeout kills the process; 3: `fake:loop` ends `timeout` |
| NFR6 Secret redaction | 1: redaction helper; 3: secret absent from stored events and output |
| NFR7 Single machine | Implicit: every layer runs on one laptop with one process and one database file |
| NFR8 Vendor independence | 1: both adapters; 6: three model sources |
| OBS1 Version health and dashboard | 1: percentiles, success and thumbs up rates, eval exclusion, period deltas, local day buckets, overview and agent metrics over a temp DB (`tests/test_observability.py`); 3: fake:hello, fake:error and fake:loop runs give the expected health, promotion marker after promote (`tests/e2e/test_observability.py`); 4: chart scales and ticks, KPI deltas, period storage (Vitest); dashboard KPIs and charts, Monitoring version comparison with prod highlighted and a promotion marker (Playwright) |
| OBS2 Cost tracking | 1: known, overridden, unknown and self-hosted prices; cache tokens reported but not priced; 3: run, version and improvement cycle cost (all k repeated eval runs plus judge and optimizer) with FORGE_PRICES; 4: run cost on the run page |
| OBS3 Step timing | 1: loop emits additive duration and token fields, timeline from recorded durations, fallback for old events, failed model calls; 3: timeline spans and durations for a real run; 4: timeline layout (Vitest), run page waterfall (Playwright) |
| OBS4 Failure breakdown | 1: classification of every category, aggregation with example runs, `eval_failed` from per-run verdicts while pass rates use the majority; 3: categories and reasons per version from real failing runs; 4: Monitoring failure lists link example runs |
| OBS5 Run search and filters | 1: every filter, sort and count over a temp DB; 3: agent and global endpoints, X-Total-Count, builder-only global route, share tokens keep their restricted view; 4: filter query building (Vitest), Runs tab filters narrow results (Playwright) |
| OBS6 Tool statistics | 1: per tool calls, error rate, p50, p95, totals, slowest calls, duration fallback; 3: tool stats from real runs |
| OBS7 Ops endpoints and logs | 1: Prometheus text format; 3: /healthz 200 and 503 (missing sandbox image), /metrics content and localhost restriction |
| AUTH1 Drafting uses structured output | 1: Claude request has `output_config.format` json_schema, no temperature, user-only messages; schema avoids unsupported constraints |
| AUTH2 Draft validation | 1: slug, known tools, prompt and rubric length, secret names, case count, unique names, splits |
| AUTH3 Retry once on invalid draft | 1: one fresh retry listing the problems; second invalid reply raises |
| AUTH4 Deterministic drafting fake | 1: `fake:grader` returns `FAKE_DRAFT` for the drafting prompt only |
| AUTH5 Drafting prompt asks for specific prompts | 1: prompt states role, working method, output, avoid, pass/fail rubrics, two thirds train |
| AUTH6 Draft endpoint creates nothing | 1: draft returned, no agent created, free slug, 400 on empty, 401 remote; 3: fake draft over the real server |
| AUTH7 Create agent with selected cases | 1 and 3: `POST /agents` with `eval_cases` creates the agent and exactly those cases |
| AUTH8 Draft review UI | 4: Vitest describe step, prefilled fields, case checkboxes and edits, secrets; Playwright drafting end to end |
| PRUNE1 Pruning preconditions | 1: 400 with fewer than 2 lessons or no train case, nothing created; human lessons get provenance |
| PRUNE2 Unhelpful means no train loss | 1: `classify` (ties unhelpful, incomplete unknown) |
| PRUNE3 Ablation cap, oldest first | 1: `select_for_ablation` |
| PRUNE4 Pruning rule | 1: `is_eligible_prune` accepts ties, rejects holdout regressions and swaps |
| PRUNE5 Lesson provenance | 1: `apply_lesson_edits_meta` records version and evidence, keeps carried entries |
| PRUNE6 Prune removes the useless lesson | 3: MARKER lesson helpful, useless lesson removed, candidate eligible, prod unchanged |
| PRUNE7 Cached results reused | 3: candidate train results are the identical ablation version's runs |
| PRUNE8 Pruning verdicts in provenance | 3: base and candidate `lesson_meta.pruning` |
| PRUNE9 Prune regression rejected | 3: removing two individually unhelpful lessons regresses every case and is rejected |
| PRUNE10 Prune UI | 4: Playwright Prune lessons, removal evidence, pruning rule, promote, Configure origin and "kept after pruning" |
| PRUNE11 Pruning on majority of k | 1: `train_passes` counts majority verdicts, fewer than k runs is incomplete; 3: `fake:flaky` k=3 prune: every version runs 3 per case, ablations train only, all runs linked to the job in cost |
| PRUNE12 Reuse copies per-run rows | 1: `_reuse_results` copies eval_run_results and passes/runs, leaves cases with own runs alone |
| PRUNE13 Prune keeps the feedback window | 1: prune jobs do not move `_last_improvement_time`; auto-improve still fires; improve jobs do move it |
| EX1 Suggested prompts | 1: stored on the agent, returned by every Agent response including the share view, replaced by PATCH and POST versions (the Version object never carries them), 422 on bad title or more than 12 (`tests/test_server.py`, `tests/test_auth_proxy.py`); 4: chips render, fill the input and run as is (Vitest `SuggestedPrompts.test.tsx`), share page chips (Playwright `deploy.spec.ts`) |
| EX2 Example agents | 1: definitions well formed, deterministic builds match committed files, splits, planted ground truth, judge rubrics grade references (`tests/test_examples.py`); 3: `load_examples.py` creates everything idempotently and seeds reach the sandbox (`tests/e2e/test_examples_loader.py`); 5b: `demo/verify_examples.py` 50 expectations |
| SET1 Settings registry and precedence | 1: environment > saved > default, empty environment is unset, invalid environment reported, validation per type, secrets never stored or shown, live vs restart (`needs_restart` until a restart), `register_setting`, call sites read live values, per endpoint keys, `.env.example` matches the registry (`tests/test_settings.py`) |
| SET2 Settings API | 1: GET groups and info, PATCH all or nothing with per key 422 errors, reset, `null` resets, localhost only (`tests/test_settings.py`); 3: PATCH `eval_repeats` changes k for the next eval without a restart, a restart setting reports `needs_restart`, environment owned settings refuse PATCH (`tests/e2e/test_settings.py`) |
| SET3 Model availability | 1: providers reachable, down, rejected key, 60 s cache and refresh with mocked transports; warnings for unknown Ollama models, unreachable endpoints and a missing Claude key (`tests/test_settings.py`); 3: a stub OpenAI compatible server on port 8203 lists models and a version save warns about a missing one (`tests/e2e/test_settings.py`) |
| SET4 Eval early stop | 1: a model not found failure runs once, records no verdicts and reports `error`; 3: `fake:missing` with eval concurrency 1 runs once for 3 cases |
| SET5 Settings page | 4: Vitest groups, source badges, per section save of changed keys, inline and server errors, reset, restart banner with Restart only in the desktop app, prices table, model picker tabs and installed check (`web/tests/unit/Settings.test.tsx`); Playwright on its own server (project `settings`, port `FORGE_E2E_PORT + 1`): change the default model and a new agent uses it, Restart required, inline errors at 390px (`web/tests/e2e/settings.spec.ts`) |
| SET6 Desktop keys | `cd desktop && npm test`: key variable names, set, replace and remove keys, environment for the server with `FORGE_DESKTOP_SECRETS`, one time move of old model choices (`desktop/test/keys.test.js`); `npm run verify` checks the main window bridge and sets the default model through `PATCH /v1/settings` |

## Adding a test

- Put it in the lowest layer that can prove the behavior.
- Name it after what it proves (`test_regression_blocks_promotion`), and cite requirement IDs in the docstring.
- No fixed sleeps: poll with a timeout. No network in layers 1 to 5.
- A bug fix lands with the test that would have caught it.
