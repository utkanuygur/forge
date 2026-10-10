# Forge testing workflow

Forge is tested in six layers (plus 5b for the example agents), ordered from fastest to slowest. Every deterministic layer runs without
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
| 3 | Full stack | `make test-e2e` | Docker | Real server + Docker + fake models: run lifecycle, SSE, uploads, versions, auth, feedback, secrets, limits, webhook, local CLI, **full improvement loop including regression blocking and auto-promotion**, repeated evals (majority of k), agent drafting, lesson pruning, example agent loader, observability metrics, cost, search, timelines, health and Prometheus endpoints | 1 to 3 min |
| 4 | Web | `make test-web` | Docker, Chromium (Playwright) | Component and hook logic (Vitest); browser flows against the real backend: create (blank, template, described and drafted), run, suggested prompt chips, feedback, versions, evaluate (k repeats), improve, prune, promote, share page hides config, dashboard, monitoring, run filters, step timeline, phone width | 1 to 3 min |
| 5 | Demo validity | `make test-demo` | Docker | Each demo eval case FAILS on the untouched repo, FAILS on a correct but convention-violating fix, PASSES on the reference fix (21 expectations) | ~1 min |
| 5b | Example validity | `make test-examples` | Docker | Every example agent's shell checker (eval cases and suggested prompt checks) FAILS on the seed, FAILS on a plausible wrong answer where one exists, PASSES on the reference solution (50 expectations); `--judge` also grades the 4 judge cases with the real grader | ~30 s |
| 6 | Live models | `make test-live` | `ANTHROPIC_API_KEY`; optional Ollama, `FORGE_VLLM_URL` | Real Claude completes a task in the sandbox; judge gives sensible verdicts; local and GPU models emit valid tool calls | 1 to 2 min, costs tokens |

`make test` runs layers 1 to 3 (skipping Docker tests automatically if Docker is unavailable).
`make test-all` runs every deterministic layer (1 to 5b). Live tests never run unless asked
(`-m live` or `FORGE_LIVE=1`).

CI (`.github/workflows/ci.yml`) runs one job on every pull request and every push to `master`: layers 1 to 3
(`pytest -m "not live"`), 4a, 4b and 5 on Ubuntu with the sandbox image built fresh. Layer 5b and the desktop
build are not in CI yet; live tests never run there.

The desktop app has its own checks outside these layers: `desktop/scripts/build-server.sh` fails the build when
any bundled binary needs a macOS newer than the app's minimum (`check-min-macos.sh`), and `cd desktop && npm run
verify` drives the app headless with Playwright's Electron support (see [desktop/README.md](desktop/README.md)).

## Parallel runs

| Command | What runs | Parallelism |
|---|---|---|
| `make test` (and `test-unit`, `test-docker`, `test-e2e`) | pytest, layers 1 to 3 | pytest-xdist, `PYTEST_WORKERS` workers (default min(CPUs, 6)), `--dist loadgroup` |
| `make test-web-e2e` | Playwright | `PW_WORKERS` workers (default 4 locally, 2 when `CI` is set) |
| `make test-fast` (`make test-all` calls it) | Python suite, Vitest, Playwright, demo checks, example checks | all five layers at once as background jobs (`scripts/test_fast.py`): output prefixed `[python]`, `[web-unit]`, `[browser]`, `[demo]`, `[examples]`, logs saved per layer, then a PASS/FAIL and duration table; exits non-zero if any layer fails. `--only`/`--skip` pick layers |
| `make test-changed BASE=master` | only the tests affected by your changes | see below |

Debugging: run serially with `uv run pytest -n 0 tests/...` (or `make test PYTEST_WORKERS=0`); a plain
`uv run pytest ...` without `-n` is serial too. Use fewer workers on a busy machine (`PYTEST_WORKERS=2`).

What keeps the suite parallel-safe (keep it that way when adding tests):

- Each xdist worker is its own pytest session: its own `tmp_path` root and its own e2e `server` (session
  fixture) on a free port with its own `FORGE_DATA_DIR`. Never hardcode a port; use `_free_port()` (or a
  `start_server(root, extra_env)` helper in `tests/e2e/conftest.py`). Never write into the repo or a fixed `/tmp` path.
- Environment changes go through `monkeypatch.setenv`, never `os.environ[...] =`.
- Every pytest process sets `FORGE_SANDBOX_LABEL=forge.test.session=<worker>-<id>`; `DockerExecutor` adds it
  as `docker run --label`, and the e2e leak check counts only containers with this worker's label, so
  other workers, the browser suite, the demo checkers or another checkout's tests never look like leaks.
- Tests that share a module- or class-scoped fixture (for example `observed` in
  `tests/e2e/test_observability.py`) are put in one `xdist_group` per module automatically
  (`tests/conftest.py`), so the fixture is built once. Other tests, e2e included, spread across workers.
  To pin a module to one worker explicitly, add `pytestmark = pytest.mark.xdist_group("name")`.
- Browser tests create their own agents with `uniqueSlug(...)`; the Playwright servers pick free ports
  (or `FORGE_E2E_PORT`, +1, +2) and a fresh data dir per invocation.

## Iterating: run only what changed

`make test-changed` (default `BASE=master`) lists the changed files (committed on the branch, staged,
unstaged and untracked), prints which tests it selected and why, then runs them in parallel through the
same runner as `test-fast`. `uv run python scripts/test_changed.py --files <paths> --dry-run` shows the
selection for any files without running anything.

| Change | Selected |
|---|---|
| A forge module | Python test modules importing it, directly or through other forge or test modules (static `ast` import graph). Core modules (`forge/server/**`, `loop`, `executors`, `db`, `types`, `providers`, `improve`, `schema.sql`) add every e2e module and every browser spec; feature modules (`drafting`, `pruning`, `provenance`, `metrics`, `pricing`, `cli`) add only the e2e modules and specs that exercise them; any other module the server imports adds every e2e module |
| A test, `conftest.py` or `helpers.py` | that test, every test importing it; a conftest selects its directory |
| `web/src/**` | `vitest related <files> --run`, plus every browser spec (specs drive pages through the browser, not imports, so they cannot be narrowed by import) |
| A browser spec | that spec alone; a shared helper or web config selects every spec |
| `demo/**`, `sandbox/**`, `forge/executors.py`, `forge/loop.py` | `demo/verify.py` and `demo/verify_examples.py`, plus tests that read `demo/` files |
| `pyproject.toml`, `uv.lock` | the whole Python suite |
| Markdown, `docs/` | nothing |

Selection is static rather than coverage based (pytest-testmon) because most of the value is in the e2e
layer, whose code runs in a server subprocess that a coverage tool would not see. It is a speedup for
iteration, not a gate: **iterate with `make test-changed`, then run the full suite (`make test-fast`, or at
least `make test`) once before merging.**

## Fake model scenarios

| Model string | Behavior | Used for |
|---|---|---|
| `fake:hello` | Narrates, writes `hello.py`, narrates, runs it with bash, finishes | Run lifecycle, traces, diffs, CLI, transcript |
| `fake:session` | Turn 1 writes `hello.py`; later turns `cat hello.py` and report what they saw and how many earlier turns the history held | Playground sessions: history and workspace carry-over |
| `fake:slow` | Runs `sleep 30` forever | Cancel and the Stop button |
| `fake:echo` | Finishes immediately echoing the input | Fast API and UI tests |
| `fake:lessons` | Creates `MARKER.txt` only when a lesson says so | Improvement loop: v1 fails `test -f MARKER.txt`, v2 passes |
| `fake:grader` | As judge: passes. As optimizer: proposes the MARKER lesson citing real failing run IDs. With the drafting prompt (`forge.drafting.DRAFT_SYSTEM`): returns the fixed, valid `FAKE_DRAFT` ("Issue triager", network on, `GITHUB_TOKEN`, 3 train and 1 holdout case) whatever the description. With an assist kind's system prompt: returns that kind's registered `fake` reply; for kind `task` the first of `FAKE_TASKS` (`forge/assist/kinds/task.py`, three fixed, valid tasks) whose prompt is not already in the request (recent run inputs, `avoid` or the current box text), so consecutive generations differ deterministically; for `eval_case` and `eval_cases` the first of `FAKE_SHELL` / `FAKE_JUDGE` (`forge/assist/kinds/eval_case.py`) not already an existing case, with the requested split and checker type (a source run's input verbatim); every other kind returns a small deterministic reply that passes its own validator (for example `instructions` appends "Always finish with a one line summary of what you changed.", `cron` parses simple English such as "weekdays at 9am") | Grader, optimizer, agent drafting and Generate buttons without API calls |
| `fake:loop` | Calls bash forever | Turn limit and `timeout` status |
| `fake:error` | Raises like a failed API | `failed` status and error events |
| `fake:missing` | Raises `ModelNotFoundError`, as if the model does not exist on its provider | Evals stop early instead of running k times per case |
| `fake:flaky` | Alternates per run: the 1st, 3rd, 5th, ... run with a given (system prompt, input) writes `FLAKY.txt`, the others do not. A process-wide counter keyed by sha256(system, input) advances on each run's first model call, so k concurrent runs of one case yield ceil(k/2) passes of `test -f FLAKY.txt` in any order (k=3: 2/3 majority pass; k=2: 1/2 tie fails). Server restart resets it; give each test its own system prompt or input | Repeated evals and the majority rule end to end |
| `fake:risky` | Runs `rm -rf build` with bash, then finishes saying whether it was allowed | Approval prompts: approve, deny, timeout and deny rules |
| `fake:copilot` | The Forge assistant's model (`forge/copilot/fake.py`). `fake:grader` answers the same way whenever it gets the assistant's system prompt, so test servers need no extra setting. "add N [train or holdout] eval cases to <slug>" (N a digit or one to ten) makes N `create_eval_case` calls in one turn (one confirmation card) named `assistant-case-<turn>-<i>` with checker `test -f hello.py`, then says what was added, or "Nothing was added" after a denial; "why did run <r_id> fail" calls `get_run` and quotes the status and error; "make me an agent that <description>" calls `draft_agent`, then `create_agent` with the draft's `config` (one confirmation card) and links the new agent, or says "Nothing was created" after a denial; "add the example agents" calls `add_example_agents`; "how do I call <slug>" calls `get_deploy_info`; secrets, API keys or tokens get a refusal; anything else a short help text | Assistant confirmation flow, tool steps and the side panel without API calls |
| `fake:tools` | Calls non-builtin tools named by the first JSON object in the input: `{"tool": "<name or prefix>", "args": {..}}` or `{"calls": [{"tool", "args"}, ..]}` (no hint: the first non-builtin tool with empty args), then finishes with `Tool results:` and every tool output | HTTP tools, MCP tools, agents calling agents |

Cost assertions are deterministic because the end-to-end server (`tests/e2e/conftest.py`) and the browser test
server (`web/playwright.config.ts`) set `FORGE_PRICES` to give the fake models a price; scripted grader responses
report fixed token counts.

## Workflow

| When | Run | Gate |
|---|---|---|
| While coding | `make test-changed` (or `make test-unit`, `uv run pytest tests/test_<area>.py`) | Must pass |
| Before each commit | `make test` | Must pass |
| Before merging or demoing | `make test-fast` (same as `make test-all`) | Must pass |
| After changing prompts, the optimizer or the demo | `make test-demo`, then `make rehearse` | Rehearsal must print `OK: held-out improved` |
| Before a live demo | `make serve` in one terminal, `make preflight` in another | Every line PASS (WARN only for optional GPU and local model) |

### Continuous integration

`.github/workflows/ci.yml` runs on every pull request, on pushes to `master` and on manual dispatch. Each
layer is its own parallel check, so the commit status names the layer that failed:

| Check | Runs | Layer |
|---|---|---|
| Lint | `compileall forge`, `tsc -b` in `web/` | static |
| Python unit | `pytest -m "not docker and not e2e and not live"` | 1 |
| Sandbox | `pytest -m "docker and not e2e"` | 2 |
| Full stack | `pytest -m e2e` | 3 |
| Web unit | `npm test` (Vitest) | 4a |
| Browser | `npm run test:e2e -- --workers=2` (Playwright, Chromium); report and traces uploaded on failure | 4b |
| Demo checks | `demo/verify.py --runner docker`, `demo/verify_examples.py` | 5, 5b |
| Desktop macOS 12 compat | `desktop/scripts/build-server.sh` on macos-14 (arm64), which ends with `check-min-macos.sh` | desktop build |

Each Docker job builds `forge-sandbox` itself through `.github/actions/sandbox-image`, which restores the layers
from the GitHub Actions cache (30 to 45 s with a warm cache, including Buildx setup; the Sandbox job writes the cache) and fails if the image is
missing, since the Docker tests would otherwise skip themselves. Building once and sharing the image as an
artifact was measured too: download plus `docker load` cost about as much per job and added a serial build job,
so the run took 15 to 25 s longer. The macOS job is billed at a higher rate, so it runs
only when `desktop/`, `forge/`, `pyproject.toml`, `uv.lock` or the workflow change (pull requests and pushes
to `master`), or on manual dispatch (`gh workflow run ci.yml --ref <branch>`). Live tests never run in CI.

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
| FR15 HTTP tools, MCP servers, agents calling agents | TOOL1 to TOOL9 below |
| TOOL1 HTTP tool schema, URL templates, query and body mapping | 1: `test_schema_validation_rejects_bad_schemas_and_bad_args`, `test_url_template_encodes_values_and_splits_query_and_body`, `test_success_non_2xx_truncation_and_body_mapping`; 3: `test_http_tool_runs_from_server_with_secret_header_and_query` |
| TOOL2 Secret headers substituted, never logged | 1: `test_secret_substitution_and_missing_secret`, `test_http_tool_through_loop_is_traced_and_secret_redacted`; 3: secret absent from events and from the Test endpoint output |
| TOOL3 SSRF protection | 1: private ranges, `localhost`, a hostname resolving to 127.0.0.1, mixed DNS answers, redirect to a private IP, IP pinning, redirect cap, headers dropped across origins; 3: `test_http_tool_to_loopback_is_blocked_by_default` |
| TOOL4 Network policy for server side tools | 1: `test_network_policy_and_private_override`, remote MCP refused without network; 3: `test_http_tool_needs_network_and_test_endpoint` |
| TOOL5 MCP stdio inside the sandbox | 1: stdio on the host with the SDK server and the SDK free fallback, timeouts, ask mode; 3: `test_mcp_stdio_server_runs_inside_the_sandbox` (cwd `/workspace`, no network) |
| TOOL6 MCP remote servers and failures | 1: `test_mcp_http_headers_policy_and_private_check`, `test_mcp_ask_mode_declined_and_failing_server_emit_errors_and_run_continues`, `test_mcp_result_mapping`; 3: `test_mcp_http_server_and_failing_server` |
| TOOL7 Agents calling agents with usage rollup | 3: `test_parent_calls_child_with_rollup_and_links`; 4: `tools.spec.ts` child run link and Called by |
| TOOL8 Depth limit and cycle detection | 1: `test_depth_limit_and_cycle_detection`; 3: `test_cycle_between_two_agents_is_refused` |
| TOOL9 Tools section in Configure | 4: `ToolsConfig.test.tsx` (schema, headers, placeholders, child run link); `tools.spec.ts` adds, validates, tests and saves an HTTP tool |
| Run transcript and narration | 1: progress updates become `assistant` events with `kind` before tool calls, progress-update request shape, environment note (`tests/test_narration.py`), CLI transcript and NO_COLOR (`tests/test_transcript.py`); 3: `forge run` prints `● Write(hello.py)`; 4: tool renderers, diff, markdown sanitization, Ctrl+O, auto-scroll (`web/tests/unit/transcript.test.tsx`), `fake:hello` transcript on run and share pages (`web/tests/e2e/transcript.spec.ts`) |
| Playground sessions | 1: history reconstruction, truncation, archive carry-over, column migration, draft exclusion (`tests/test_sessions.py`); 3: two-turn `fake:session`, cancel of `fake:slow` and `fake:loop`, 409 while busy, draft excluded and saved as a version (`tests/e2e/test_sessions.py`); 4: composer keys (`web/tests/unit/Composer.test.tsx`), two-turn session, Stop, Test from Configure (`web/tests/e2e/playground.spec.ts`) |
| Share and API conversations | 1: visitor scoping (other visitors, token without visitor, builder sessions, forged visitor ids, other agents: 404 or 401), each share limit with 429 and Retry-After, daily rollover at 00:00 UTC with a fake clock (`share_limits.now`), expiry to read only (410), env defaults and per agent overrides, monthly budget and per-run cap on share turns, API key conversation on a chosen version, Slack thread sessions with limit and expiry (`tests/test_share_sessions.py`); 3: two-turn `fake:session` share conversation where turn 2 reads turn 1's file, visitor B gets 404 on A's session and runs, limits answer 429, API key conversation, Slack `app_mention` replies in a thread continue one conversation and a full thread gets a note (`tests/e2e/test_share_sessions.py`); 4: empty state, Enter and Shift+Enter, visitor stored and sent, steps collapsed with the final answer, thumbs, files and download link, recent conversations, limit copy (`web/tests/unit/ShareChat.test.tsx`), two turns, Files, thumbs, a second browser context cannot see the conversation, Stop at 390 px, daily limit inline in dark mode, limits in the Deploy tab (`web/tests/e2e/share-chat.spec.ts`) |
| AS Forge assistant | 1: every tool's schema, one-line description and risk, argument validation, one confirmation per model turn and nothing runs before it, deny and repeat answers, `execute` refuses an unapproved write, invalid writes fail before asking, 15 step and 2 minute limits, one turn at a time and Stop, secret refusal and redaction of tool output, docs_lookup, builder-only auth with private sessions and API keys limited to their agent, history rebuild, metering under the agent or the `copilot` bucket (`tests/test_copilot.py`); agent tools: model check (only models the app can run), draft then create on one card, create validation before asking, examples, deploy info without the key or share token in the chat, HTTP tool and MCP server versions that refuse literal credentials, rollback (`tests/test_copilot_agent_tools.py`); 3: add 3 cases after approval, deny creates none, explain a `fake:error` run (`tests/e2e/test_copilot.py`); 4: panel toggle, Cmd+J and Ctrl+J, context header, starters, tool step rendering, confirm card, SSE parsing (`web/tests/unit/copilot.test.tsx`), add cases from the Evaluations tab and see them appear, phone sheet (`web/tests/e2e/copilot.spec.ts`) |
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
| GOV1 Approval policy rules | 1: bash regex presets, file globs, deny precedence, validation (`tests/test_governance.py`); 1: invalid policy is a 400 |
| GOV2 Approve executes the paused call | 1: loop pause and approve; 3: `test_approve_executes_the_paused_call_and_notifies_the_webhook` (status `awaiting_approval`, share token 403, 409 after decision); 4: GOV10 approve |
| GOV3 Deny returns `Denied by reviewer: <note>`, run continues | 1: loop deny; 3: `test_deny_with_note_returns_error_and_the_run_continues`; 4: GOV10 deny |
| GOV4 Timeout auto-denies | 1: `test_timeout_auto_denies_and_the_run_continues` (1 s timeout); 3: `test_timeout_auto_denies` |
| GOV5 Deny rules never execute | 1 and 3: deny rule tests, no approval events |
| GOV6 Pricing and per-run cap | 1: budgets, run cost and metrics share `forge.pricing.cost` (FORGE_PRICES respected, cache tokens not priced), stored `runs.cost_usd` read by views and metrics; 1 and 3: priced `fake:loop` ends `budget_exceeded` with the cap and spend in the error |
| GOV7 Monthly budget | 1: month-to-date math and 402 on runs, evals, improve; 3: `test_monthly_budget_refuses_new_runs_with_402`, `test_improvement_stops_when_the_budget_runs_out`; 4: GOV11 banner and refused run |
| GOV8 80% warnings | 1 and 3: `budget_warning` for `run` and `month` scopes; 4: banner (GOV11) and trace row (Vitest) |
| GOV9 Restart and auth | 1: restart marks `awaiting_approval` runs failed (interrupted) and approvals expired; share token cannot approve; 3: approval webhook |
| GOV10 Approval card UI | 4: Vitest `Governance.test.tsx`; Playwright `governance.spec.ts` (live card, header and Runs tab badges, approve, deny with note) |
| GOV11 Policy editor and caps UI | 4: Vitest presets, edits, timeout; Playwright saves presets, mode, run cap and monthly budget, meter and banner |
| GOV12 Ask rules in eval runs | 1: an `ask` rule in an eval run is an immediate deny (`Denied by policy during evaluation: <rule>`), no approval row, no pause |
| GOV13 Approval wait timing | 1: `approval_wait_ms` on the tool_result, `duration_ms` excludes it, the timeline has an `approval` span and `approval_ms` outside `tool_ms`; metrics count `budget_exceeded` as finished and failing and `awaiting_approval` as in progress |
| TRG1 Cron parsing and next fire times | 1: `test_triggers_unit.py` fields, names, steps, macros, invalid expressions, day of month or weekday, month ends, leap years, time zones, DST spring forward and fall back; 4: `cron.test.ts` mirrors the same cases |
| TRG2 GitHub webhook | 1: signature (incl. GitHub's test vector), issue, label, comment command, PR and push filters, bots ignored, comment back with retry and recorded failure (mock transport); 3: `test_triggers.py` signed payload starts a run, unsigned and wrongly signed get 401, comment command filter, comment back to a local mock API |
| TRG3 Slack | 1: v0 signature and 5 minute replay window, mrkdwn conversion, truncation, slash command reply, url_verification, app_mention dedupe and bot filter; 3: slash command answers within 3 s and `response_url` (local receiver) gets the final output |
| TRG4 Input templates | 1: known fields only, braces escape, no attribute access, field and total limits; 3: rendered input reaches the run |
| TRG5 Hook rate limit and tunnel access | 1: sliding window limiter; 429 with Retry-After; hooks accepted with proxy headers while builder routes are refused |
| TRG6 Scheduler | 1: once per slot across a restart, skip while running (configurable), missed slots older than 10 minutes recorded, long downtime fires the latest slot once, disabled, rate limit; 3: fake clock file, one fire per slot, missed record, next slot fires |
| TRG7 Trigger builder API | 1: CRUD, validation, GitHub secret shown once and rotated, tokens never returned, localhost or API key only, test fire with sample payload, history, version pin |
| TRG8 Triggers UI | 4: cron preview text and next times (Vitest); Playwright creates a schedule with preview, toggles it, tests it; creates a GitHub trigger, sees the secret once, a signed delivery shows up in recent runs |
| TRG9 API base allowlist | 1: create and update refuse bases off https://api.github.com / https://slack.com (scheme, port, userinfo, look-alike hosts); FORGE_TRIGGER_API_HOSTS widens it; call-time recheck fails delivery |
| TRG10 Triggers under governance | 1: spent monthly budget records a `402:` fire error and starts nothing; an ask rule pauses a GitHub-triggered run, the comment posts only after approval |
| ROUTES Router order | 1: one endpoint each of governance, authoring, observability (api and ops), tools and triggers answers before the SPA catch-all; trigger and agent sources count as production, filter and label Prometheus (`tests/test_routes.py`) |
| EX1 Suggested prompts | 1: stored on the agent, returned by every Agent response including the share view, replaced by PATCH and POST versions (the Version object never carries them), 422 on bad title or more than 12 (`tests/test_server.py`, `tests/test_auth_proxy.py`); 4: chips render, fill the input and run as is (Vitest `SuggestedPrompts.test.tsx`), share page chips (Playwright `deploy.spec.ts`) |
| EX2 Example agents | 1: definitions well formed, deterministic builds match committed files, splits, planted ground truth, judge rubrics grade references (`tests/test_examples.py`); 3: `load_examples.py` creates everything idempotently and seeds reach the sandbox (`tests/e2e/test_examples_loader.py`); 5b: `demo/verify_examples.py` 50 expectations |
| GEN1 Assist agent context | 1: file listing capped at 200 with a count of the rest, excerpts prefer README, top-level modules, CSV headers and log openings within the 12k char cap, long system prompts condensed, recent inputs deduped and capped, secret names only (`tests/test_assist.py`) |
| GEN2 Assist registry and engine | 1: registration rejects duplicate kinds, bad names, loose schemas and reused system prompts; every envelope schema avoids unsupported constraints; one fresh retry lists the problems and a second invalid reply raises (502 over HTTP); secret values are redacted from results and never sent to the model |
| GEN3 Structured output, metered | 1: Claude gets `output_config.format` json_schema (the envelope) and no temperature; each call with an agent is a `grader_calls` row with kind `assist:<kind>`, shown as `cost.assist` in agent metrics; kinds without an agent run from localhost only and are not metered |
| GEN4 Assist access | 1 and 3: builder (localhost or API key) gets `kind`, `version` and `diagnostics`; a share token gets only result and rationale (title, prompt, rationale on the alias), always on prod, only for `share_allowed` kinds, and its 11th call in an hour across kinds and the alias is 429 with Retry-After; no credentials is 401 (`tests/e2e/test_assist.py`) |
| GEN5 Kind `task` | 1: avoid, current text, variety and hint in the request; empty, over 1200 chars and near duplicate (normalized sequence ratio or word overlap at 0.85) prompts rejected; 3: the fake task runs and the next generation differs |
| GEN6 Generate buttons | 4: Vitest generic `AssistButton` (kind, slug, version and input posted, custom label, Undo only with `onUndo` and dropped on edit, inline errors; `web/tests/unit/AssistButton.test.tsx`) and `GenerateTaskButton` (loading state, fill and focus, Undo for 10 seconds, inline error, Alt+G, mounted in RunForm; `web/tests/unit/GenerateTaskButton.test.tsx`); Playwright Generate on the Runs tab fills the task box and the run succeeds, and the share page generates with its token (`web/tests/e2e/generate.spec.ts`) |
| GEN7 Kinds `eval_case` and `eval_cases` | 1: context lists existing cases, the agent's shell checker style, split, type, focus and the holdout hint; `from_run_id` brings the run's input, reply and feedback (other agent's run is 400); validation of names, duplicates, splits, shell style (`set -euo pipefail`, FAIL lines, no network commands offline) and judge rubrics (Pass only if, numbered criteria); balanced batches (`tests/test_assist_eval_case.py`); 3: drafts save nothing until added, added shell checkers FAIL on the untouched seed in the sandbox, from a run plus feedback then `save-as-eval` (`tests/e2e/test_assist.py`) |
| GEN8 Configuration kinds | 1: `instructions` (feedback, failing cases, unchanged and leak checks), `rubric`, `seed_file` (paths, CSV, JSON, Python, credentials), `version_note` (diff against base, no change is 400), `approval_rules` (policy validation, known tools, no repeats), `budget` (stats from run costs, cap at least p95), `http_tool` (tool validation, `{{SECRET}}` headers, literal credentials redacted before the model) each with its fake; routes save nothing (`tests/test_assist_kinds.py`) |
| GEN9 Deploy kinds | 1: `cron` (parse, zone, 3 future fire times, no agent needed), `trigger_template` (allowed fields only, at least one placeholder) (`tests/test_assist_kinds.py`); 3: generated cron is accepted by a schedule trigger (`tests/e2e/test_assist.py`) |
| GEN10 Generate buttons everywhere | 4: Vitest for the new buttons and the Generate several review list; Playwright Evaluations > Generate several > Add selected adds the checked cases, and Configure > instructions Generate shows a diff to accept (`web/tests/e2e/assist.spec.ts`) |
| EX3 One click examples | 1: discovery in the checkout and in a simulated frozen bundle (`sys.frozen`, `sys._MEIPASS` laid out by `forge.examples.stage`), staging leaves out solutions and build files, install creates agent, seed, cases and prompts, model override, 409 then `?replace=1` sync and `promote=1`, install-all skips installed, builder only (`tests/test_examples_install.py`); 3: install-all over HTTP then a suggested prompt of log triage runs on `fake:hello` in the sandbox, listing shows installed, second install is 409 (`tests/e2e/test_examples_install.py`); 4: empty state card, Add all, Choose modal with counts, Installed badge, model select and errors (Vitest `Examples.test.tsx`), Add all on an empty server then Log triage opens, modal at 390px (Playwright `examples.spec.ts` on the second, empty server at `FORGE_E2E_PORT + 1`); desktop: `npm run verify` checks `GET /v1/examples` in the app |
| SET1 Settings registry and precedence | 1: environment > saved > default, empty environment is unset, invalid environment reported, validation per type, secrets never stored or shown, live vs restart (`needs_restart` until a restart), `register_setting`, call sites read live values, per endpoint keys, `.env.example` matches the registry (`tests/test_settings.py`) |
| SET2 Settings API | 1: GET groups and info, PATCH all or nothing with per key 422 errors, reset, `null` resets, localhost only (`tests/test_settings.py`); 3: PATCH `eval_repeats` changes k for the next eval without a restart, a restart setting reports `needs_restart`, environment owned settings refuse PATCH (`tests/e2e/test_settings.py`) |
| SET3 Model availability | 1: providers reachable, down, rejected key, 60 s cache and refresh with mocked transports; warnings for unknown Ollama models, unreachable endpoints and a missing Claude key (`tests/test_settings.py`); 3: a stub OpenAI compatible server on a free port lists models and a version save warns about a missing one (`tests/e2e/test_settings.py`) |
| SET4 Eval early stop | 1: a model not found failure runs once, records no verdicts and reports `error`; 3: `fake:missing` with eval concurrency 1 runs once for 3 cases |
| SET5 Settings page | 4: Vitest groups, source badges, per section save of changed keys, inline and server errors, reset, restart banner with Restart only in the desktop app, prices table, model picker tabs and installed check (`web/tests/unit/Settings.test.tsx`); Playwright on its own server (project `settings`, port `FORGE_E2E_PORT + 2`): change the default model and a new agent uses it, Restart required, inline errors at 390px (`web/tests/e2e/settings.spec.ts`) |
| SET6 Desktop keys | `cd desktop && npm test`: key variable names, set, replace and remove keys, environment for the server with `FORGE_DESKTOP_SECRETS`, one time move of old model choices (`desktop/test/keys.test.js`); `npm run verify` checks the main window bridge and sets the default model through `PATCH /v1/settings` |

## Adding a test

- Put it in the lowest layer that can prove the behavior.
- Name it after what it proves (`test_regression_blocks_promotion`), and cite requirement IDs in the docstring.
- No fixed sleeps: poll with a timeout. No network in layers 1 to 5.
- A bug fix lands with the test that would have caught it.
