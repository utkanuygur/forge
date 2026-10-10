"""Live test against a vLLM (OpenAI-compatible) server, e.g. the one in gpu/.

FORGE_VLLM_URL is the base URL including /v1; FORGE_VLLM_MODEL picks the model (default: the first served).
"""

from __future__ import annotations

import os

import pytest

from tests.live.conftest import assert_hello_done, openai_compat_models, run_hello_task

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("FORGE_VLLM_URL"), reason="FORGE_VLLM_URL not set"),
]


async def test_vllm_model_emits_valid_bash_tool_call_and_completes_task():
    """FR12, NFR8: a self-hosted GPU model served by vLLM completes a trivial task with valid tool calls."""
    base_url = os.environ["FORGE_VLLM_URL"].rstrip("/")
    models = openai_compat_models(base_url)
    if not models:
        pytest.skip(f"no models served at {base_url}")
    model = os.environ.get("FORGE_VLLM_MODEL") or models[0]
    result, evs, diff = await run_hello_task(f"openai_compat:{base_url}|{model}")
    assert any(e.type == "tool_call" and e.payload["name"] == "bash" for e in evs)
    assert_hello_done(result, evs, diff)
