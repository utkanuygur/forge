"""Live-model helpers. Every test here carries the `live` marker, so a plain `pytest` skips them; run with
`pytest -m live` (or FORGE_LIVE=1). Each test also skips itself when its endpoint or key is unavailable."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import httpx
import pytest

from forge.executors import DockerExecutor
from forge.loop import run_agent
from forge.providers import make_provider
from forge.types import AgentVersion, Event, RunResult
from tests.conftest import docker_unavailable

TASK = "Create a file hello.py that prints hi, then run it with bash to confirm it works."


def require_docker() -> None:
    if why := docker_unavailable():
        pytest.skip(why)


def openai_compat_models(base_url: str) -> list[str] | None:
    """Model ids served at an OpenAI-compatible base URL, or None if nothing answers there."""
    try:
        r = httpx.get(base_url.rstrip("/") + "/models", timeout=3)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


async def run_hello_task(model: str, *, max_turns: int = 12) -> tuple[RunResult, list[Event], str]:
    """Runs TASK with a real model in a Docker sandbox; returns the result, the trace and the workspace diff."""
    require_docker()
    version = AgentVersion(id="v_live", agent_id="ag_live", n=1, model=model,
                           system_prompt="You are a careful software engineer. Use the tools to do the task.")
    evs: list[Event] = []

    async def emit(e: Event) -> None:
        evs.append(e)

    ex = DockerExecutor()
    try:
        await ex.start({}, {})
        result = await run_agent(version, TASK, run_id="r_live", provider=make_provider(model), executor=ex,
                                 emit=emit, max_turns=max_turns, max_seconds=300)
        with tempfile.TemporaryDirectory() as d:
            await ex.export_workspace(str(Path(d) / "ws.tar.gz"), str(Path(d) / "diff.patch"))
            diff = (Path(d) / "diff.patch").read_text()
    finally:
        await ex.stop()
    return result, evs, diff


def assert_hello_done(result: RunResult, evs: list[Event], diff: str) -> None:
    trace = [(e.type, e.payload.get("name")) for e in evs]
    assert result.status == "succeeded", (result, trace)
    assert "hello.py" in diff and "hi" in diff, diff
    calls = [e.payload for e in evs if e.type == "tool_call"]
    assert calls and all(isinstance(c["args"], dict) and "_raw" not in c["args"] for c in calls), calls
    ran = [e.payload for e in evs if e.type == "tool_result" and e.payload["name"] == "bash"]
    assert any("hi" in r["output"] and not r["is_error"] for r in ran), ran


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or default
