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
| `fake:risky` | Runs `rm -rf build` with bash, then finishes saying whether it was allowed | Approval prompts: approve, deny, timeout and deny rules |

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
| FR15 HTTP tools | Not implemented (P2); no test |
| NFR1 Isolation | 2: network none, non-root user, limits; 3: no leaked containers after the suite |
| NFR2 Reproducibility | 3: every run and eval result records its version |
| NFR3 Live trace | 3: SSE delivers events before the run ends; 4: trace renders live |
| NFR5 Bounded execution | 1: turn and wall-clock limits; 2: command timeout kills the process; 3: `fake:loop` ends `timeout` |
| NFR6 Secret redaction | 1: redaction helper; 3: secret absent from stored events and output |
| NFR7 Single machine | Implicit: every layer runs on one laptop with one process and one database file |
| NFR8 Vendor independence | 1: both adapters; 6: three model sources |
| GOV1 Approval policy rules | 1: bash regex presets, file globs, deny precedence, validation (`tests/test_governance.py`); 1: invalid policy is a 400 |
| GOV2 Approve executes the paused call | 1: loop pause and approve; 3: `test_approve_executes_the_paused_call_and_notifies_the_webhook` (status `awaiting_approval`, share token 403, 409 after decision); 4: GOV10 approve |
| GOV3 Deny returns `Denied by reviewer: <note>`, run continues | 1: loop deny; 3: `test_deny_with_note_returns_error_and_the_run_continues`; 4: GOV10 deny |
| GOV4 Timeout auto-denies | 1: `test_timeout_auto_denies_and_the_run_continues` (1 s timeout); 3: `test_timeout_auto_denies` |
| GOV5 Deny rules never execute | 1 and 3: deny rule tests, no approval events |
| GOV6 Pricing and per-run cap | 1: fallback table, free providers, FORGE_PRICES, `forge.pricing` hook; 1 and 3: priced `fake:loop` ends `budget_exceeded` with the cap and spend in the error |
| GOV7 Monthly budget | 1: month-to-date math and 402 on runs, evals, improve; 3: `test_monthly_budget_refuses_new_runs_with_402`, `test_improvement_stops_when_the_budget_runs_out`; 4: GOV11 banner and refused run |
| GOV8 80% warnings | 1 and 3: `budget_warning` for `run` and `month` scopes; 4: banner (GOV11) and trace row (Vitest) |
| GOV9 Restart and auth | 1: restart marks `awaiting_approval` runs failed (interrupted) and approvals expired; share token cannot approve; 3: approval webhook |
| GOV10 Approval card UI | 4: Vitest `Governance.test.tsx`; Playwright `governance.spec.ts` (live card, header and Runs tab badges, approve, deny with note) |
| GOV11 Policy editor and caps UI | 4: Vitest presets, edits, timeout; Playwright saves presets, mode, run cap and monthly budget, meter and banner |

## Adding a test

- Put it in the lowest layer that can prove the behavior.
- Name it after what it proves (`test_regression_blocks_promotion`), and cite requirement IDs in the docstring.
- No fixed sleeps: poll with a timeout. No network in layers 1 to 5.
- A bug fix lands with the test that would have caught it.
