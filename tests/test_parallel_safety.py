"""Guards for running the suite in parallel (pytest-xdist): sandbox containers carry a per-process label."""

from __future__ import annotations

import os

import pytest

from forge import executors
from forge.types import ExecResult


async def _docker_run_argv(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[list[str]] = []

    async def fake_run(argv: list[str], **kw: object) -> ExecResult:
        seen.append(argv)
        return ExecResult(exit_code=0, stdout="cid\n", stderr="")

    monkeypatch.setattr(executors, "_run", fake_run)
    await executors.DockerExecutor().start({}, {})
    return seen[0]


async def test_docker_executor_adds_optional_label(monkeypatch):
    """FORGE_SANDBOX_LABEL becomes `docker run --label`, so a test worker can find exactly its own containers."""
    monkeypatch.setenv("FORGE_SANDBOX_LABEL", "forge.test.session=gw3-abc")
    argv = await _docker_run_argv(monkeypatch)
    i = argv.index("--label")
    assert argv[i + 1] == "forge.test.session=gw3-abc" and i < argv.index(os.environ.get("FORGE_SANDBOX_IMAGE", "forge-sandbox"))

    monkeypatch.delenv("FORGE_SANDBOX_LABEL")
    assert "--label" not in await _docker_run_argv(monkeypatch)


def test_each_pytest_process_has_its_own_sandbox_label():
    """tests/conftest.py sets a label naming this xdist worker (or `main` when serial)."""
    label = os.environ["FORGE_SANDBOX_LABEL"]
    assert label.startswith(f"forge.test.session={os.environ.get('PYTEST_XDIST_WORKER', 'main')}-")
