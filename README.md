# Forge

Create, deploy and continuously improve general-purpose AI agents.

An agent on Forge is a versioned configuration that deploys like code. Every run executes in an isolated
Docker sandbox and leaves a complete trace. Feedback and failing evaluations turn into a candidate
version, and a candidate is promoted only when it passes more cases, including held-out cases it never
saw, with zero regressions.

- **Create:** instructions, model, tools (bash, file read/write/edit), network policy, secrets, seed files; or describe the agent in plain words and review a drafted configuration with starter evaluation cases.
- **Tools:** typed HTTP tools with secret headers and SSRF protection, MCP servers (remote, or local stdio inside the sandbox), and agents that call other agents, each call traced as a linked child run.
- **Deploy:** immutable versions with a production pointer; REST API with per-agent keys; share links for colleagues; webhooks; a `forge` CLI that runs agents locally against your own files.
- **Improve:** feedback from every surface, evaluation suites with train and held-out splits, an optimizer that proposes evidence-linked lessons, and a no-regression promotion rule. Lesson pruning retires lessons that never helped (leave-one-out ablation, kept only at equal quality), and every lesson records where it came from.
- **Observe:** a dashboard and per-agent monitoring (version health next to eval pass rates, cost per run, version and improvement cycle, failure breakdown, tool statistics), run search, step timelines, `/healthz`, Prometheus `/metrics` and JSON run logs, all from Forge's own database.
- **Models:** Claude, an open-source model served on GPUs with vLLM (`gpu/vllm_modal.py`), or a local model through any OpenAI-compatible server such as Ollama.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), Node 20+, Docker.

```bash
make setup                 # Python + web dependencies, sandbox image
cp .env.example .env       # add ANTHROPIC_API_KEY
make serve                 # http://localhost:8000
make preflight             # checks the environment end to end
```

Load the reference agent and its evaluation suite, then watch it improve:

```bash
uv run python demo/load.py
make rehearse
```

Expose it to others: `cloudflared tunnel --url http://localhost:8000`.

## Documentation

| File | Contents |
|---|---|
| [CONTRACT.md](CONTRACT.md) | HTTP API, objects, conventions, promotion rule |
| [docs/triggers.md](docs/triggers.md) | Schedules, GitHub webhooks and Slack commands that start runs |
| [TESTING.md](TESTING.md) | Six test layers, commands and requirement traceability |
| [demo/README.md](demo/README.md) | The reference agent, its conventions and evaluation cases |

## Tests

`make test-all` runs every deterministic layer (no API keys needed: models are replaced by scripted
`fake:` scenarios while the server, database, sandboxes, CLI and browser are real). `make test-live`
runs against real models.

## Desktop app

`desktop/` packages Forge as a macOS app for Apple Silicon: it checks Docker, builds the sandbox image on
first run, starts a bundled server on 127.0.0.1 and opens the UI in a native window, with the Claude API
key kept encrypted in the Keychain and an optional public link through cloudflared.

```bash
cd desktop && npm install && npm run dist    # desktop/dist/Forge-<version>-arm64.dmg
```

The app is not notarized, so the first launch needs right click > Open, or
`xattr -dr com.apple.quarantine /Applications/Forge.app`. See [desktop/README.md](desktop/README.md).
