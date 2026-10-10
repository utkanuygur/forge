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
| 3 | Full stack | `make test-e2e` | Docker | Real server + Docker + fake models: run lifecycle, SSE, uploads, versions, auth, feedback, secrets, limits, webhook, local CLI, **full improvement loop including regression blocking and auto-promotion** | 1 to 3 min |
| 4 | Web | `make test-web` | Docker, Chromium (Playwright) | Component and hook logic (Vitest); browser flows against the real backend: create, run, feedback, versions, evaluate, improve, promote, share page hides config, phone width | 1 to 3 min |
| 5 | Demo validity | `make test-demo` | Docker | Each demo eval case FAILS on the untouched repo, FAILS on a correct but convention-violating fix, PASSES on the reference fix (21 expectations) | ~1 min |
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
| `fake:grader` | As judge: passes. As optimizer: proposes the MARKER lesson citing real failing run IDs | Grader and optimizer without API calls |
| `fake:loop` | Calls bash forever | Turn limit and `timeout` status |
| `fake:error` | Raises like a failed API | `failed` status and error events |
| `fake:tools` | Calls non-builtin tools named by the first JSON object in the input: `{"tool": "<name or prefix>", "args": {..}}` or `{"calls": [{"tool", "args"}, ..]}` (no hint: the first non-builtin tool with empty args), then finishes with `Tool results:` and every tool output | HTTP tools, MCP tools, agents calling agents |

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
| FR5 Share page and API | 1 and 3: API key, share-token scope, 401 cases; 4: share page runs and hides configuration |
| FR6 File inputs and artifacts | 3: JSON seed files, multipart file and tarball uploads, diff and archive contents; 4: diff view |
| FR7 Complete traces | 3: gapless ordered events ending in `run_finished`; SSE resume with `after` |
| FR8 Feedback from every surface | 3: API and CLI feedback stored against run and version; 4: thumbs down persists |
| FR9 Eval cases with splits | 3 and 4: eval run reports per-split scores; 5: checkers are valid |
| FR10 Improve and promotion rule | 1: `is_eligible` (equal score not eligible, regression blocks); 3: full loop v1 to v2 eligible, regression rejected, auto-promote promotes; 4: improve and promote in the UI |
| FR11 Save run as eval case | 3: save-as-eval; 4: saved case appears in Evaluations |
| FR12 Model choice | 1: provider message mapping and retries; 6: Claude, local model, GPU model |
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
| NFR1 Isolation | 2: network none, non-root user, limits; 3: no leaked containers after the suite |
| NFR2 Reproducibility | 3: every run and eval result records its version |
| NFR3 Live trace | 3: SSE delivers events before the run ends; 4: trace renders live |
| NFR5 Bounded execution | 1: turn and wall-clock limits; 2: command timeout kills the process; 3: `fake:loop` ends `timeout` |
| NFR6 Secret redaction | 1: redaction helper; 3: secret absent from stored events and output |
| NFR7 Single machine | Implicit: every layer runs on one laptop with one process and one database file |
| NFR8 Vendor independence | 1: both adapters; 6: three model sources |

## Adding a test

- Put it in the lowest layer that can prove the behavior.
- Name it after what it proves (`test_regression_blocks_promotion`), and cite requirement IDs in the docstring.
- No fixed sleeps: poll with a timeout. No network in layers 1 to 5.
- A bug fix lands with the test that would have caught it.
