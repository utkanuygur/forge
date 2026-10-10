# Pending doc and site sync, per branch

What has to change in the docs, `.env.example`, the website and the TDD when each pending branch merges into
master. Written 2026-10-10 against master 5af0139 plus feat/sync-docs. Rerun the sync agent after the final merge
and tick these off. TDD items refer to the section letters in [tdd-updates.md](tdd-updates.md).

General checks after every merge:

- [ ] `make help` lists every new target with a correct help string.
- [ ] `.env.example` lists every new `os.environ` read (`git grep -nE "environ|getenv" -- forge`).
- [ ] TESTING.md traceability rows name tests that exist (`git grep` each name).
- [ ] CONTRACT.md: the new section does not contradict the "Auth" section, Objects, or "Repository layout".
- [ ] README feature bullets and docs/test-cases.md mention only what is on master.
- [ ] Website (forge-site, branch site-sync or later): remove the "Next release" badge for features that are now
      in a published release (only when a dmg containing them is published, not at merge time).

## feat/governance (approvals and spending caps; also in integrate/batch)

- [ ] README: Deploy or a new "Govern" bullet: approval policies (ask or deny rules on bash commands and file
      paths), per-run spending caps, monthly budgets per agent with an 80% warning and 402 when reached.
- [ ] CONTRACT: branch adds "Governance" section. After merge, fold into "Auth": approvals POST is builder or API
      key, share token gets 403; `GET /runs/{id}/approvals` allows share tokens. Add `awaiting_approval` and
      `budget_exceeded` to the Run `status` comment and `approval_requested`, `approval_resolved`,
      `budget_warning` to `forge/types.py` `EventType` comments if not already there.
- [ ] CONTRACT Pricing: the branch keeps a fallback table in `forge/budget.py`; make sure the two price tables
      agree (claude-opus-5-5 4/20, claude-sonnet-5-5 2/10, claude-haiku-5-5 0.10/0.50).
- [ ] 1.3 of the TDD and README: Forge now enforces budgets (see TDD item 0, Non-goals).
- [ ] TESTING: governance rows (branch adds them); check names with grep.
- [ ] TDD: section F.
- [ ] Website: drop "Next release" on Approvals and Spending caps (and FAQ answers) once released.

## feat/tools (HTTP tools, MCP servers, agents calling agents; also in integrate/batch)

- [ ] `.env.example`: add `FORGE_ALLOW_PRIVATE_HTTP` (1 disables the SSRF address check; development and tests
      only) and `FORGE_MCP_CONNECT_TIMEOUT` (seconds to connect to an MCP server).
- [ ] CONTRACT Conventions: delete the bullet "`http_tools` ... is accepted and stored on versions but not yet
      offered to the model". Repository layout gets `forge/tools/` (the branch adds it).
- [ ] `forge/types.py`: delete "the loop does not offer it to the model yet" from the `HttpTool` docstring (the
      branch replaces `HttpTool`; resolve the conflict in favor of the branch's model and keep the event notes).
- [ ] TESTING: FR15 row "Not on master" becomes the branch's tool tests; add MCP and callable agent rows.
- [ ] README: Create bullet: "tools (bash, file read/write/edit, typed HTTP tools, MCP servers, other agents)".
- [ ] web `TOOL_NAMES` and Configure docs if they list only the four built-ins.
- [ ] Run sources: add `agent` (child runs) to the CONTRACT Run `source` comment.
- [ ] TDD: section G; Future work "Tool ecosystem" moves to done.
- [ ] Website: drop "Next release" on More tools, the Create card list item and the FAQ MCP sentence once
      released.

## integrate/batch (merge of governance and tools)

- [ ] Do the governance and tools items above; check the combined CONTRACT reads as one document (Tools section
      references Governance "Run cost"; the Pricing paragraph now says cost is stored).

## feat/triggers

- [ ] `.env.example`: `FORGE_PUBLIC_URL` (public base URL used in hook paths and reply links),
      `FORGE_SCHEDULER` (0 disables the scheduler), `FORGE_SCHEDULER_INTERVAL` (seconds, default 15),
      `FORGE_TRIGGER_RATE_LIMIT` (runs per hour per trigger, default 30), `FORGE_TRIGGER_RETRY_BACKOFF`
      (seconds between GitHub and Slack delivery retries), `FORGE_SLACK_RESPONSE_URL_PREFIXES` (allowed Slack
      response_url prefixes, comma separated), `FORGE_TEST_CLOCK_FILE` (tests only: scheduler reads the time from
      this file).
- [ ] README: Deploy bullet: "triggers: cron schedules, signed GitHub webhooks and Slack commands"; add
      docs/triggers.md to the Documentation table (the branch adds the file).
- [ ] CONTRACT: Run `source` comment gains `schedule`, `github`, `slack`; "Auth" section gains the hooks rule
      (no auth besides the signature; work through the tunnel). IDs bullet gains `tr_`, `tf_`.
- [ ] README tunnel sentence: hooks work through the tunnel too.
- [ ] TESTING: trigger rows (branch adds them).
- [ ] TDD: section H.
- [ ] Website: drop "Next release" on Triggers and the Deploy card sentence once released.

## feat/run-view (Claude Code style transcript, Playground sessions, cancel, narration)

- [ ] CONTRACT Suggested prompts: put "the Playground" back into "shown as clickable chips above the run input
      (Runs tab, the Playground, and the share page)" (feat/sync-docs removed it because it was not on master).
- [ ] docs/test-cases.md line 11: back to "go to the Runs tab (or the Playground)".
- [ ] CONTRACT Run `source` comment gains `playground`; Run `status` gains `cancelled`; Run gains `session_id`,
      `turn_index`; `assistant` payload gains `kind`. IDs bullet gains `s_`.
- [ ] `forge/types.py`: the branch edits the `assistant` payload comment; keep its `{"text", "kind"}` line and drop
      the sync-docs note "(a turn's text; a turn with only tool calls emits no assistant event)" only if narration
      changes that rule (with narration a tool-only turn can emit progress `assistant` events).
- [ ] README: Observe or Create bullet: live Claude Code style transcript; new "Playground" bullet (multi-turn
      sessions, draft config without a version, Stop).
- [ ] `forge/server/templates.py` and `build_system_prompt`: the branch steers agents to narrate and use file
      tools; check CONTRACT and TDD 6.1 "Prompt assembly" describe the new environment note.
- [ ] TESTING layer 4 "Proves": transcript and Playground flows.
- [ ] TDD: section I.
- [ ] Website: drop "Next release" on Live transcript and Playground once released.

## feat/playground-extras (workspace files, forge chat; builds on run-view)

- [ ] README Command line section: add `uv run forge chat <slug>[@vN]` (REPL over Playground sessions; Ctrl+C
      cancels the turn; `/files`, `/open <path>`, `/new`, `/exit`).
- [ ] `.env.example`: `NO_COLOR` is honored by `forge chat` (standard; optional to list). `FORGE_URL` is already
      listed for preflight; check whether `forge chat --url` also reads it and update its one line meaning.
- [ ] CONTRACT "Auth": workspace file endpoints allow share tokens only for runs whose source is `share` (stricter
      than the other run reads); keep both statements consistent.
- [ ] TESTING: rows for the files endpoints and `forge chat`.
- [ ] TDD: section J.
- [ ] Website: Features "From your terminal" and the Get started terminal block can show `forge chat` again (the
      site-sync branch replaced it with `forge login` and `forge run`), and the badge can go once released.

## feat/generate-task (Generate button, assist endpoint)

- [ ] `.env.example`: `FORGE_ASSIST_SHARE_LIMIT` (Generate calls per hour per share token, default 10).
- [ ] README: Create or Runs bullet: "Generate fills the task box with a realistic sample task".
- [ ] CONTRACT "Auth": `/assist/{kind}` rules for share tokens (kinds with `share_allowed`, rate limited).
- [ ] TESTING: assist rows (branch adds some); verify names.
- [ ] TDD: section O.
- [ ] Website: the release notes for v0.1.2 already say "Generate buttons for every field" are coming; keep the
      site wording to what the branch ships (one kind, `task`).

## feat/ci-split (parallel CI checks per layer)

- [ ] TESTING: the branch adds a "Continuous integration" subsection. Delete the sync-docs paragraph that starts
      "CI (`.github/workflows/ci.yml`) runs one job" (it describes the old single job and says 5b and the desktop
      build are not in CI, which the branch changes).
- [ ] README Tests section: replace "CI ... runs layers 1 to 5 on every push to master and every pull request"
      with "CI runs every deterministic layer, including 5b and a macOS 12 compatibility build of the desktop
      server, as separate parallel checks".
- [ ] TDD: section N second half.

## Desktop and release (no branch; housekeeping)

- [ ] `desktop/package.json` `version` is still `0.1.0` on master although v0.1.1 and v0.1.2 are published; bump
      it to the next release version before building the next dmg (sync-docs could not edit `desktop/`).
- [ ] Each new release: notes on the code repository's Releases page (never on forge-site), then update the
      website's "Next release" badges.

## Status after integrate/batch (all branches above merged)

Done in integrate/batch: `.env.example` lists every `FORGE_*` the code reads; README Create/Deploy/Govern/Try
bullets, tunnel sentence, `forge chat`; CONTRACT IDs, Run `source` and `status` comments, Auth (share tokens reach
share runs only; webhooks signed and SSRF checked), Conventions `http_tools` bullet replaced, Suggested prompts
mention the Playground again; `forge/types.py` HttpTool and `assistant` comments; docs/test-cases.md Playground;
one price table (forge/pricing.py, budget.py has no fallback). Still open: TDD sections F to J, the website
badges, and a read-through of CONTRACT as one document.

