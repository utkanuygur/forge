# Forge

Create, deploy and continuously improve general-purpose AI agents.

An agent on Forge is a versioned configuration that deploys like code. Every run executes in an isolated
Docker sandbox and leaves a complete trace. Feedback and failing evaluations turn into a candidate
version, and a candidate is promoted only when it passes more cases, including held-out cases it never
saw, with zero regressions.

- **Create:** instructions, model, tools (bash, file read/write/edit, typed HTTP tools, MCP servers, other agents), network policy, secrets, seed files; or describe the agent in plain words and review a drafted configuration with starter evaluation cases.
- **Tools:** typed HTTP tools with secret headers and SSRF protection, MCP servers (remote, or local stdio inside the sandbox), and agents that call other agents, each call traced as a linked child run.
- **Deploy:** immutable versions with a production pointer; REST API with per-agent keys; share links for colleagues; signed webhooks (`callback_url`); triggers (cron schedules, signed GitHub webhooks and Slack commands); a `forge` CLI that runs agents locally against your own files.
- **Govern:** approval policies (ask or deny rules on bash commands, file paths and HTTP, MCP or agent tools; a person approves or denies paused calls in the run view), per-run spending caps, and monthly budgets per agent with an 80% warning and 402 once reached.
- **Try:** a live, Claude Code style run transcript and a Playground for multi-turn sessions on a version or an unsaved draft, with Stop, a workspace file browser and Generate for sample tasks.
- **Improve:** feedback from every surface, evaluation suites with train and held-out splits, each case run k times (3 by default) and judged by majority, an optimizer that proposes evidence-linked lessons, and a no-regression promotion rule. Lessons only fill gaps in your instructions and never override them; when the evidence points at an instruction itself, the improvement quotes it as an instruction conflict for you to review and ends `needs_author` instead of writing a lesson. Lesson pruning retires lessons that never helped (leave-one-out ablation, kept only at equal quality), and every lesson records where it came from.
- **Generate buttons for every field:** evaluation cases (one, several for review, or from a run and its feedback), judge rubrics, improved instructions shown as a diff, seed files, version notes, approval rules, budgets from measured costs, HTTP tools from a curl command, cron from plain English and trigger templates. Each one fills the field and saves nothing until you do; Undo and Option+G work everywhere.
- **Observe:** a dashboard and per-agent monitoring (version health next to eval pass rates, cost per run, version and improvement cycle, failure breakdown, tool statistics), run search, step timelines, `/healthz`, Prometheus `/metrics` and JSON run logs, all from Forge's own database.
- **Models:** Claude (agents default to `anthropic:claude-sonnet-5-5`; `.env.example` sets the grader, which judges evals, runs the optimizer and drafts agents, to `anthropic:claude-opus-5-5`), an open-source model served on GPUs with vLLM (`gpu/vllm_modal.py`), or a local model through any OpenAI-compatible server such as Ollama.
- **Assistant:** a chat side panel on every page (Cmd+J or Ctrl+J) that explains Forge and operates it for you: it looks up agents, runs, evals and costs at once, and asks before anything that changes data or spends money ("add three eval cases about refunds", "why did this run fail?", "ask before rm -rf").
- **Examples:** four example agents (log triage, data analyst, docs writer, repo maintainer) with suggested prompts shown as one-click chips and verified eval cases.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), Node 20+, Docker.

```bash
make setup                 # Python + web dependencies, sandbox image
cp .env.example .env       # add ANTHROPIC_API_KEY
make serve                 # http://localhost:8000
make preflight             # in another terminal: checks the environment end to end
```

Load the reference agent and its evaluation suite, then watch it improve:

```bash
uv run python demo/load.py
make rehearse
```

Or load all four example agents (log triage, data analyst, docs writer, repo maintainer) with starter
prompts and verified eval cases: click **Examples** on the Agents page (or **Add all** on the empty Agents
page, also in the desktop app), or run `make examples`, then see [docs/test-cases.md](docs/test-cases.md).

Share links are conversations: a colleague who opens `/s/<slug>?share=<token>` chats with the production version,
follow up messages keep the earlier answers and files, and each visitor only ever sees their own conversations.
Every link is limited per conversation (20 messages), per visitor (3 open conversations), per day (200
messages) and in concurrency (2 at once); change them per agent in the Deploy tab or globally with
`FORGE_SHARE_SESSION_TURNS`, `FORGE_SHARE_SESSIONS_PER_VISITOR`, `FORGE_SHARE_DAILY_TURNS` and
`FORGE_SHARE_CONCURRENT_TURNS` (conversations go read only after `FORGE_SESSION_IDLE_HOURS`, default 24). API
callers hold conversations with the same endpoints (`POST /v1/agents/<slug>/sessions`, then
`POST /v1/sessions/<id>/messages`), and a Slack thread that mentions the bot continues one conversation. See
CONTRACT.md "Share and API conversations".

Expose it to others: `cloudflared tunnel --url http://localhost:8000`. Requests through a tunnel count as
remote: share links, the API with an agent key and trigger hooks (`/v1/hooks/github|slack/{id}`, checked by
their signatures) work, the builder UI does not.

### Local models

Run an agent on a local model with [Ollama](https://ollama.com):

```bash
ollama pull qwen3:4b-instruct
```

Larger Qwen models (for example `qwen3:14b`) do better on multi-step coding tasks if your machine can run them.
Then set the agent's model to `openai_compat:http://localhost:11434/v1|qwen3:4b-instruct` (it is one of the presets in
the model picker). Any OpenAI-compatible server works the same way: `openai_compat:<base_url>|<model>`.

### Command line

`uv sync` installs the `forge` command. It runs any agent version on your own machine against a directory,
asking before each command or write, and syncs the trace back to the server (local runs never count toward
evals):

```bash
uv run forge login http://localhost:8000 <agent-api-key>
uv run forge run repo-maintainer "add a refund endpoint" --dir .   # --auto, --sandbox, --model, --no-sync
```

`uv run forge chat <slug>[@vN]` opens a multi-turn Playground session in the terminal (Ctrl+C stops the turn;
`/files`, `/open <path>`, `/new`, `/exit`). Both commands print the same Claude Code style transcript.
`forge feedback` rates a run, and `forge sync` uploads traces buffered while the server was unreachable.

## Configuration

Every setting (default and grader models, OpenAI compatible endpoints such as Ollama and vLLM, sandbox
image and limits, run limits, eval repeats, concurrency, logs, metrics, model prices, the public URL) is
on the Settings page (gear icon), with a description, where its value comes from, and whether it applies
at once or after a restart. Status cards show the Claude key, Ollama, Docker and a GPU endpoint. A variable
set in the environment or `.env` wins and is shown read only, so keep `.env` for API keys and the data
directory. API keys are never stored in the database: put them in `.env` for the dev server, or in the
desktop app's Settings window (Keychain). `.env.example` is generated from the registry in
`forge/settings.py`; see CONTRACT.md "Settings".

## Documentation

| File | Contents |
|---|---|
| [CONTRACT.md](CONTRACT.md) | HTTP API, objects, conventions, promotion rule |
| [docs/triggers.md](docs/triggers.md) | Schedules, GitHub webhooks and Slack commands that start runs |
| [TESTING.md](TESTING.md) | Test layers, commands, CI and requirement traceability |
| [demo/README.md](demo/README.md) | The reference agent, its conventions and evaluation cases |
| [docs/test-cases.md](docs/test-cases.md) | What to try first: example agents, suggested prompts, expected results, eval splits |
| [desktop/README.md](desktop/README.md) | The macOS app: build, install, how it works, testing |
| [.env.example](.env.example) | Every setting and environment variable Forge reads (generated from the settings registry) |
| [docs/tech-stack.md](docs/tech-stack.md) | Technology stack: every layer with exact versions, status per branch, dependencies, security choices |

## Tests

`make test-all` runs the deterministic layers 1 to 5 (no API keys needed: models are replaced by scripted
`fake:` scenarios while the server, database, sandboxes, CLI and browser are real); `make test-examples`
checks the example agents' checkers. `make test-live` runs against real models. CI
(`.github/workflows/ci.yml`) runs layers 1 to 5 on every push to master and every pull request.

## Desktop app

`desktop/` packages Forge as a macOS app for Apple Silicon (macOS 12 or later): it checks Docker, builds the
sandbox image on first run, starts a bundled server on 127.0.0.1 and opens the UI in a native window, with
the Claude API key kept encrypted in the Keychain and an optional public link through cloudflared.

```bash
cd desktop && npm install && npm run dist    # desktop/dist/Forge-<version>-arm64.dmg
```

Built apps are published on this repository's Releases page. The app is not notarized, so the first launch
needs Open Anyway in System Settings > Privacy & Security, or
`xattr -dr com.apple.quarantine /Applications/Forge.app`. See [desktop/README.md](desktop/README.md).
