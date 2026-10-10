"""Secret redaction with a real sandbox: a bash command that prints a secret env var.

No fake scenario prints the environment, so this drives `run_agent` with a scripted FakeProvider and a real
DockerExecutor instead of going through the server (the server path is covered in test_runs.py).
"""

from __future__ import annotations

import json

import pytest

from forge.executors import DockerExecutor
from forge.loop import run_agent
from forge.providers import FakeProvider
from forge.types import AgentVersion, Event, ModelResponse, ToolCall

pytestmark = pytest.mark.docker

SECRET = "sk-live-9f8e7d6c5b4a"


async def test_secret_env_var_printed_by_bash_is_redacted_in_events_and_output():
    """NFR6, FR3: the secret is available to commands as an env var, but its value never reaches events or the
    final output; [REDACTED] stands in for it."""
    version = AgentVersion(id="v_sec", agent_id="ag_sec", n=1, system_prompt="test", model="fake:x")
    provider = FakeProvider([
        ModelResponse(tool_calls=[ToolCall(id="c1", name="bash", args={"cmd": "echo token=$API_TOKEN"})]),
        ModelResponse(text=f"The token is {SECRET}."),
    ])
    evs: list[Event] = []

    async def emit(e: Event) -> None:
        evs.append(e)

    ex = DockerExecutor()
    try:
        await ex.start({}, {"API_TOKEN": SECRET})
        assert (await ex.exec("echo $API_TOKEN")).stdout.strip() == SECRET  # really in the sandbox env
        result = await run_agent(version, "print the token", run_id="r_sec", provider=provider, executor=ex,
                                 emit=emit, secrets={"API_TOKEN": SECRET})
    finally:
        await ex.stop()
    dumped = json.dumps([e.model_dump() for e in evs])
    assert SECRET not in dumped and SECRET not in result.output
    out = next(e for e in evs if e.type == "tool_result").payload["output"]
    assert "token=[REDACTED]" in out
    assert result.output == "The token is [REDACTED]."
