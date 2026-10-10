"""Live local-model test against an OpenAI-compatible server such as Ollama.

FORGE_LOCAL_BASE_URL (default http://localhost:11434/v1) and FORGE_LOCAL_MODEL (default qwen3:4b-instruct).
"""

from __future__ import annotations

import pytest

from tests.live.conftest import assert_hello_done, env, openai_compat_models, run_hello_task

pytestmark = pytest.mark.live

BASE_URL = env("FORGE_LOCAL_BASE_URL", "http://localhost:11434/v1")
MODEL = env("FORGE_LOCAL_MODEL", "qwen3:4b-instruct")


async def test_local_model_emits_valid_bash_tool_call_and_completes_task():
    """FR12, NFR8, NFR7: a local open-weights model drives the agent loop through the OpenAI-compatible adapter:
    it emits well-formed tool calls (including bash) and completes a trivial task in the sandbox."""
    models = openai_compat_models(BASE_URL)
    if models is None:
        pytest.skip(f"no OpenAI-compatible server at {BASE_URL}")
    if MODEL not in models:
        pytest.skip(f"model {MODEL!r} not served at {BASE_URL} (available: {models}); set FORGE_LOCAL_MODEL")
    result, evs, diff = await run_hello_task(f"openai_compat:{BASE_URL}|{MODEL}")
    assert any(e.type == "tool_call" and e.payload["name"] == "bash" for e in evs)
    assert_hello_done(result, evs, diff)
