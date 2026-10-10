"""Forge assistant: the in-app chat that explains Forge and operates it through tools (see CONTRACT.md "Assistant").

prompt.py   system prompt and per-message context block
tools.py    the tool registry: thin wrappers over Forge's HTTP API, each with a schema and a risk (read | write)
engine.py   one user message: the model and tool loop, confirmation gating for write tools, limits, persistence
fake.py     `fake:copilot`, the deterministic model used by tests
docs.py     docs_lookup search over README, CONTRACT, TESTING and docs/
evalgen.py  generate_eval_cases adapter over the assist registry
"""
