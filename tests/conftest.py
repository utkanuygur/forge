"""Shared pytest configuration: markers and automatic skipping.

Markers (registered in pyproject.toml):
  docker  needs a Docker daemon and the forge-sandbox image; skipped when either is missing
  e2e     starts a real Forge server subprocess (also needs Docker, so it implies `docker`)
  live    calls real model APIs; never runs unless `-m live` is given or FORGE_LIVE=1

A plain `pytest` runs unit tests plus docker and e2e tests when Docker is available.

Parallel runs (pytest-xdist, `make test`): every test is xdist-safe. Each worker gets its own tmp dirs and
e2e server (session fixtures are per worker), and labels the sandbox containers it starts with
FORGE_SANDBOX_LABEL so the e2e leak check only counts its own. Modules whose tests share a module- or
class-scoped fixture are kept on one worker (`--dist loadgroup`); run serially with `-n 0` to debug.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import uuid

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


def pytest_configure(config: pytest.Config) -> None:
    # One label per pytest process (each xdist worker is one), set before any test starts a container.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    os.environ["FORGE_SANDBOX_LABEL"] = f"forge.test.session={worker}-{uuid.uuid4().hex[:8]}"


def _shares_fixture_state(item: pytest.Item) -> bool:
    """True when the test uses a module-, class- or package-scoped fixture (state shared with its siblings)."""
    info = getattr(item, "_fixtureinfo", None)
    return any(defs and defs[-1].scope in ("module", "class", "package")
               for defs in (info.name2fixturedefs.values() if info else ()))


# tryfirst: the xdist_group markers must exist before xdist's own hook reads them under --dist loadgroup.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    live = live_enabled(config)
    for item in items:
        if item.module and not item.get_closest_marker("xdist_group") and _shares_fixture_state(item):
            item.add_marker(pytest.mark.xdist_group(item.module.__name__))
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
