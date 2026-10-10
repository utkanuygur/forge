"""End-to-end local runner: the `forge` CLI runs an agent version on this machine and syncs its trace."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.e2e.helpers import assert_gapless, events

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]


def forge_cli(*args: str, home: Path) -> subprocess.CompletedProcess:
    """`uv run forge ...` with HOME pointed at a temp dir so the user's ~/.config/forge is never touched."""
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv not on PATH")
    env = {k: v for k, v in os.environ.items() if not k.startswith("FORGE_")}
    env.update({
        "HOME": str(home),
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv"),
        "PYTHONPATH": str(REPO),  # independent of the editable-install .pth file
    })
    return subprocess.run([uv, "run", "--project", str(REPO), "forge", *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)


def test_cli_local_run_edits_directory_and_syncs_trace_without_eval_results(api, server, make_agent, tmp_path):
    """FR13, FR7, FR12: `forge login` then `forge run <slug> ... --dir <tmp> --auto` runs the agent's version
    locally (model fake:hello), creates hello.py in the directory, and uploads the trace: the run appears via
    GET /runs/{id} with source local and a gapless trace. Local runs never create eval results."""
    agent = make_agent("cli")
    slug = agent["slug"]
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()

    login = forge_cli("login", server.url, agent["api_key"], home=home)
    assert login.returncode == 0, login.stderr
    cfg = home / ".config" / "forge" / "config.json"
    assert cfg.exists() and oct(cfg.stat().st_mode & 0o777) == "0o600"

    res = forge_cli("run", slug, "create hello.py and run it", "--dir", str(work), "--auto", home=home)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (work / "hello.py").read_text() == "print('hello from forge')\n"
    m = re.search(r"succeeded · 3 turns · run (r_[0-9a-f]{12})", res.stdout)
    assert m, res.stdout
    run_id = m.group(1)

    run = api.get(f"/runs/{run_id}").json()
    assert run["source"] == "local" and run["status"] == "succeeded" and run["version_n"] == 1
    assert run["output"] == "Created and ran hello.py." and run["turns"] == 3 and run["eval_case_id"] is None
    evs = events(api, run_id)
    assert_gapless(evs)
    assert any(e["type"] == "tool_result" and "hello from forge" in e["payload"]["output"] for e in evs)
    assert run_id in {r["id"] for r in api.get(f"/agents/{slug}/runs").json()}

    fb = api.post(f"/runs/{run_id}/feedback", json={"rating": -1, "comment": "local nit"}).json()
    assert fb["source"] == "local"  # local feedback is labeled and still feeds improvement
    results = api.get(f"/agents/{slug}/evals/results", params={"version": 1}).json()
    assert results["results"] == [] and results["train"]["passed"] == 0
    assert not list((home / ".config" / "forge").glob("outbox/*.jsonl"))  # nothing left buffered
