# Forge

Create, deploy and continuously improve general-purpose AI agents.

An agent on Forge is a versioned configuration that deploys like code. Every run executes in an isolated
Docker sandbox and leaves a complete trace. Feedback and failing evaluations turn into a candidate
version, and a candidate is promoted only when it passes more cases, including held-out cases it never
saw, with zero regressions.

- **Create:** instructions, model, tools (bash, file read/write/edit), network policy, secrets, seed files.
- **Deploy:** immutable versions with a production pointer; REST API with per-agent keys; share links for colleagues; webhooks; a `forge` CLI that runs agents locally against your own files.
- **Improve:** feedback from every surface, evaluation suites with train and held-out splits, an optimizer that proposes evidence-linked lessons, and a no-regression promotion rule.
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
