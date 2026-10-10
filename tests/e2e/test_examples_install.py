"""End-to-end: the one click example install (POST /v1/examples/install-all) against a real server and sandbox,
then a suggested prompt of an installed example runs with the fake:hello model."""

from __future__ import annotations

import pytest

from tests.e2e.helpers import tar_members, wait_run

pytestmark = pytest.mark.e2e

SLUGS = ["data-analyst", "docs-writer", "log-triage", "repo-maintainer"]


def test_install_all_then_run_a_suggested_prompt(api):
    before = {i["key"]: i["installed"] for i in api.get("/examples").json()}
    assert sorted(before) == SLUGS

    r = api.post("/examples/install-all", json={"model": "fake:hello"})
    assert r.status_code == 200, r.text
    body = r.json()
    installed = {a["slug"] for a in body["installed"]}
    # Another e2e test may have loaded some examples already; those are skipped, never duplicated.
    assert installed | set(body["skipped"]) == set(SLUGS) and not installed & set(body["skipped"])
    assert set(body["skipped"]) == {k for k, v in before.items() if v}
    assert all(a["api_key"].startswith("fk_") for a in body["installed"])

    listing = api.get("/examples").json()
    assert all(i["installed"] for i in listing)
    assert {a["slug"] for a in api.get("/agents").json()} >= set(SLUGS)

    # Installing one again is a clear 409.
    again = api.post("/examples/log-triage/install")
    assert again.status_code == 409 and "already exists" in again.json()["detail"]

    agent = api.get("/agents/log-triage").json()
    prompt = agent["suggested_prompts"][0]["prompt"]
    run = api.post("/agents/log-triage/runs", json={"input": prompt, "wait": True}).json()
    run = wait_run(api, run["id"])
    assert run["status"] == "succeeded", run
    files = tar_members(api.get(f"/runs/{run['id']}/artifacts").content)
    assert {"README.md", "logs/nginx/access.log"} <= set(files)
    assert len(api.get("/agents/log-triage/evals").json()) == 4
