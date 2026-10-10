"""End-to-end fixtures: a real `uvicorn forge.server.app:app` subprocess backed by Docker sandboxes.

The server runs with a temporary FORGE_DATA_DIR, FORGE_EXECUTOR=docker and deterministic fake models
(`fake:hello` for agents, `fake:grader` for the judge and optimizer), so the whole suite needs no API keys.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.conftest import docker_unavailable
from tests.e2e.helpers import poll

REPO = Path(__file__).resolve().parents[2]
SANDBOX_FILTER = ["--filter", "ancestor=" + os.environ.get("FORGE_SANDBOX_IMAGE", "forge-sandbox")]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sandbox_containers() -> set[str]:
    out = subprocess.run(["docker", "ps", "-q", *SANDBOX_FILTER], capture_output=True, text=True, timeout=30)
    return set(out.stdout.split())


class Server:
    def __init__(self, url: str, data_dir: Path, log: Path) -> None:
        self.url, self.data_dir, self.log = url, data_dir, log

    def log_tail(self, n: int = 60) -> str:
        try:
            return "\n".join(self.log.read_text(errors="replace").splitlines()[-n:])
        except FileNotFoundError:
            return "(no server log)"


@pytest.fixture(scope="session")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    if why := docker_unavailable():
        pytest.skip(why)
    root = tmp_path_factory.mktemp("forge-e2e")
    data_dir, log = root / "data", root / "server.log"
    port = _free_port()
    keep = {"FORGE_SANDBOX_IMAGE"}
    env = {k: v for k, v in os.environ.items()
           if (not k.startswith("FORGE_") or k in keep) and k != "ANTHROPIC_API_KEY"}
    env.update({
        "FORGE_DATA_DIR": str(data_dir),
        "FORGE_EXECUTOR": "docker",
        "FORGE_DEFAULT_MODEL": "fake:hello",
        "FORGE_GRADER_MODEL": "fake:grader",
        # uvicorn trusts X-Forwarded-For from this address; tests use it to look like a remote client.
        "FORWARDED_ALLOW_IPS": "127.0.0.1",
        "PYTHONUNBUFFERED": "1",
        # Do not rely on the editable-install .pth (macOS may flag it hidden, which Python then skips).
        "PYTHONPATH": os.pathsep.join(p for p in (str(REPO), os.environ.get("PYTHONPATH", "")) if p),
    })
    before = sandbox_containers()
    logf = log.open("w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "forge.server.app:app", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=root, env=env, stdout=logf, stderr=subprocess.STDOUT,
    )
    srv = Server(f"http://127.0.0.1:{port}", data_dir, log)

    def healthy() -> bool | None:
        if proc.poll() is not None:
            raise AssertionError(f"server exited with {proc.returncode}:\n{srv.log_tail()}")
        try:
            return httpx.get(f"{srv.url}/v1/templates", timeout=2).status_code == 200 or None
        except httpx.HTTPError:
            return None

    try:
        poll(healthy, timeout=30, interval=0.1, what="server health")
        yield srv
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        logf.close()
    # No sandbox container started during the session may outlive it. Containers other processes started
    # (for example a concurrent browser suite) are excluded by diffing against the pre-session snapshot.
    leaked: set[str] = set()

    def no_leaks() -> bool | None:
        nonlocal leaked
        leaked = sandbox_containers() - before
        return not leaked or None

    try:
        poll(no_leaks, timeout=20, interval=0.5, what="sandbox containers to exit")
    except AssertionError:
        raise AssertionError(f"leaked sandbox containers: {sorted(leaked)}\n{srv.log_tail()}") from None


@pytest.fixture(scope="session")
def api(server: Server) -> Iterator[httpx.Client]:
    """Builder client: localhost, no credentials."""
    with httpx.Client(base_url=server.url + "/v1", timeout=120) as c:
        yield c


@pytest.fixture
def make_agent(api: httpx.Client) -> Callable[..., dict[str, Any]]:
    """Creates an agent with a unique slug; returns the Agent JSON including the one-time `api_key`."""

    def make(prefix: str = "agent", *, model: str = "fake:hello", system_prompt: str = "You are a test agent.",
             **kw: Any) -> dict[str, Any]:
        slug = f"{prefix}-{uuid.uuid4().hex[:6]}"
        r = api.post("/agents", json={"slug": slug, "name": prefix, "system_prompt": system_prompt,
                                      "model": model, **kw})
        assert r.status_code == 200, r.text
        return r.json()

    return make

