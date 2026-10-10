"""End-to-end: demo/load_examples.py creates every example agent, its suggested prompts and eval cases, and is
idempotent; the seeds reach the sandbox and the shell checkers run there."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.helpers import tar_members, wait_evals, wait_run

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
SLUGS = ["data-analyst", "docs-writer", "log-triage", "repo-maintainer"]


def load(server, *extra: str) -> str:
    r = subprocess.run([sys.executable, str(REPO / "demo" / "load_examples.py"), "--base-url", server.url, *extra],
                       capture_output=True, text=True, timeout=300, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def expected(slug: str) -> tuple[list[dict], list[dict]]:
    sys.path.insert(0, str(REPO / "demo"))
    from load_examples import resolve

    ex = resolve(REPO / "demo" / "examples" / slug)
    return ex["spec"]["suggested_prompts"], ex["cases"]


def test_load_examples_creates_everything_and_is_idempotent(server, api):
    """FR1, FR9: every example agent exists with its suggested prompts and all eval cases; a second run changes
    nothing (no new version, no case replaced); a changed prompt list is synced without a new version."""
    first = load(server)
    for slug in SLUGS:
        assert f"{slug}: created" in first or f"{slug}: exists" in first
        agent = api.get(f"/agents/{slug}").json()
        prompts, cases = expected(slug)
        assert agent["suggested_prompts"] == prompts
        got = {c["name"]: c for c in api.get(f"/agents/{slug}/evals").json()}
        assert set(got) == {c["name"] for c in cases}
        for c in cases:
            assert got[c["name"]]["checker"] == c["checker"] and got[c["name"]]["split"] == c["split"]
    versions = {slug: api.get(f"/agents/{slug}").json()["prod_version"] for slug in SLUGS}

    second = load(server)
    for slug in SLUGS:
        line = next(x for x in second.splitlines() if x.startswith(f"{slug}:"))
        assert "already matches" in line and "suggested prompts unchanged" in line and " 0 added, 0 replaced" in line
        agent = api.get(f"/agents/{slug}").json()
        assert agent["prod_version"] == versions[slug] and len(agent["versions"]) == versions[slug]
        assert len(api.get(f"/agents/{slug}/evals").json()) == len(expected(slug)[1])

    # Someone edits the prompts in the UI or API; the loader restores the definition without a new version.
    assert api.patch("/agents/log-triage", json={"suggested_prompts": []}).status_code == 200
    third = load(server, "--only", "log-triage")
    assert "suggested prompts updated" in third and "data-analyst" not in third
    assert api.get("/agents/log-triage").json()["suggested_prompts"] == expected("log-triage")[0]
    assert api.get("/agents/log-triage").json()["prod_version"] == versions["log-triage"]


def test_example_seed_reaches_the_sandbox_and_checkers_run_there(server, api):
    """FR6, FR9: a run of a loaded example sees its seed files, and the example's shell checkers execute in the
    sandbox and fail with their own message when the agent (here fake:hello) does not do the task."""
    load(server, "--only", "log-triage")
    prompt = expected("log-triage")[0][0]["prompt"]
    run = api.post("/agents/log-triage/runs", json={"input": prompt, "wait": True}).json()
    run = wait_run(api, run["id"])
    assert run["status"] == "succeeded", run
    files = tar_members(api.get(f"/runs/{run['id']}/artifacts").content)
    assert {"README.md", "logs/nginx/access.log", "logs/db/postgresql.log"} <= set(files)
    assert files["logs/nginx/access.log"] == (REPO / "demo/examples/log-triage/seed/logs/nginx/access.log").read_bytes()

    prod = api.get("/agents/log-triage").json()["prod_version"]
    api.post("/agents/log-triage/evals/run", json={"version": prod})
    res = wait_evals(api, "log-triage", prod, n_results=4, timeout=240)
    assert res["train"]["passed"] == 0 and res["holdout"]["passed"] == 0
    assert all("answer.json was not written" in r["reason"] for r in res["results"]), json.dumps(res)[:800]
