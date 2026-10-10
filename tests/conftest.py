"""Shared pytest configuration: markers and automatic skipping.

Markers (registered in pyproject.toml):
  docker  needs a Docker daemon and the forge-sandbox image; skipped when either is missing
  e2e     starts a real Forge server subprocess (also needs Docker, so it implies `docker`)
  live    calls real model APIs; never runs unless `-m live` is given or FORGE_LIVE=1

A plain `pytest` runs unit tests plus docker and e2e tests when Docker is available.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess

import pytest

SANDBOX_IMAGE = os.environ.get("FORGE_SANDBOX_IMAGE", "forge-sandbox")

# Existing Docker tests in tests/test_runtime.py are tagged here instead of editing that file.
_DOCKER_TESTS = {"test_docker_executor", "test_docker_loop_end_to_end"}


@functools.cache
def docker_unavailable() -> str | None:
    """None when Docker and the sandbox image are usable, else the reason they are not."""
    if not shutil.which("docker"):
        return "docker CLI not found"
    try:
        if subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode != 0:
            return "docker daemon not running (`docker info` failed)"
        if subprocess.run(["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True,
                          timeout=20).returncode != 0:
            return f"docker image {SANDBOX_IMAGE!r} missing (build it: docker build -t {SANDBOX_IMAGE} sandbox)"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"docker check failed: {exc}"
    return None


def live_enabled(config: pytest.Config) -> bool:
    if os.environ.get("FORGE_LIVE") == "1":
        return True
    expr = config.getoption("markexpr", "") or ""
    return "live" in expr and "not live" not in expr


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    live = live_enabled(config)
    for item in items:
        if item.name.split("[")[0] in _DOCKER_TESTS and item.module and item.module.__name__.endswith("test_runtime"):
            item.add_marker(pytest.mark.docker)
        if item.get_closest_marker("e2e") and not item.get_closest_marker("docker"):
            item.add_marker(pytest.mark.docker)

        if item.get_closest_marker("live") and not live:
            item.add_marker(pytest.mark.skip(reason="live model test: run with `pytest -m live` or FORGE_LIVE=1"))
        elif item.get_closest_marker("docker") and (why := docker_unavailable()):
            item.add_marker(pytest.mark.skip(reason=why))


@pytest.fixture(autouse=True)
def _no_model_list_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """In-process model availability checks (agent creation warnings) never reach a real provider in tests:
    every provider looks unreachable unless a test installs its own transport. Live tests are left alone."""
    if request.node.get_closest_marker("live"):
        return
    import httpx

    from forge import models_available

    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network disabled in tests", request=req)

    monkeypatch.setattr(models_available, "TRANSPORT", httpx.MockTransport(refuse))
    models_available.clear_cache()
