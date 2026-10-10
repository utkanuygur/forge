"""Playground sessions x governance and observability: budgets, approvals, cancel, draft exclusion, statuses."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from forge import db, governance, metrics
from forge.policy import PRESETS


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_EXECUTOR", "local")
    monkeypatch.setenv("FORGE_SCHEDULER", "0")
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    monkeypatch.delenv("FORGE_PRICES", raising=False)
    from forge.server.app import app

    with TestClient(app, client=("127.0.0.1", 5000)) as c:
        yield c


def agent(client, slug: str, **kw) -> dict:
    r = client.post("/v1/agents", json={"slug": slug, "name": slug, "system_prompt": "s", "model": "fake:echo", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def poll(fn, timeout: float = 10.0):
    end = time.time() + timeout
    while time.time() < end:
        if (v := fn()) is not None:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_playground_turns_respect_the_monthly_budget(client):
    agent(client, "pg-budget", monthly_budget_usd=0)
    s = client.post("/v1/agents/pg-budget/sessions", json={}).json()
    r = client.post(f"/v1/sessions/{s['id']}/messages", json={"input": "hi"})
    assert r.status_code == 402 and "Monthly budget of $0.00 reached" in r.json()["detail"]
    assert db.session_runs(s["id"]) == []


def test_cancel_while_awaiting_approval_denies_it_and_frees_the_sandbox(client):
    """A Playground turn pauses on an ask rule (approval card in the transcript); Stop cancels the run, resolves
    the pending approval as denied (by `cancel`), never runs the call and stops the sandbox."""
    from forge.server import runs

    agent(client, "pg-risky", model="fake:risky", approval_policy={"rules": PRESETS["rm_rf"]})
    s = client.post("/v1/agents/pg-risky/sessions", json={}).json()
    run = client.post(f"/v1/sessions/{s['id']}/messages", json={"input": "clean up"}).json()
    poll(lambda: client.get(f"/v1/runs/{run['id']}").json()["status"] == "awaiting_approval" or None)
    [ap] = governance.list_approvals(run["id"])
    assert ap["status"] == "pending"
    stopped = client.post(f"/v1/runs/{run['id']}/cancel").json()
    assert stopped["status"] == "cancelled" and stopped["error"] == "Stopped by user"
    ap = governance.get_approval(ap["id"])
    assert (ap["status"], ap["resolved_by"]) == ("denied", "cancel")
    types = [e["type"] for e in db.list_events(run["id"])]
    assert "tool_result" not in types and types[-1] == "run_finished"
    resolved = next(e["payload"] for e in db.list_events(run["id"]) if e["type"] == "approval_resolved")
    assert resolved["decision"] == "deny" and resolved["by"] == "cancel"
    assert run["id"] not in runs.get_manager().tasks and not runs.get_manager().approvals.is_pending(ap["id"])
    # the session can continue after the stop
    nxt = client.post(f"/v1/sessions/{s['id']}/messages", json={"input": "again"}).json()
    assert nxt["turn_index"] == 1
    poll(lambda: client.get(f"/v1/runs/{nxt['id']}").json()["status"] == "awaiting_approval" or None)
    assert client.post(f"/v1/runs/{nxt['id']}/cancel").json()["status"] == "cancelled"


def test_draft_session_runs_stay_out_of_run_lists_and_metrics(client):
    a = agent(client, "pg-draft")
    s = client.post("/v1/agents/pg-draft/sessions", json={"draft": {"system_prompt": "try this"}}).json()
    r = client.post(f"/v1/sessions/{s['id']}/messages", json={"input": "hi", "wait": True}).json()
    assert r["source"] == "playground" and r["status"] == "succeeded"
    assert client.get("/v1/agents/pg-draft/runs").json() == []
    assert client.get("/v1/agents/pg-draft/metrics").json()["summary"]["runs"] == 0
    saved = client.post(f"/v1/sessions/{s['id']}/save-version", json={}).json()
    assert saved["system_prompt"] == "try this"
    v = client.post("/v1/agents/pg-draft/sessions", json={}).json()  # a session on prod is production traffic
    client.post(f"/v1/sessions/{v['id']}/messages", json={"input": "hi", "wait": True})
    listed = client.get("/v1/agents/pg-draft/runs").json()
    assert [x["source"] for x in listed] == ["playground"] and a["slug"] == "pg-draft"


def test_saving_a_draft_keeps_the_base_governance_and_tool_settings(client):
    agent(client, "pg-keep", approval_policy={"rules": PRESETS["git_push"]}, max_run_cost_usd=0.5)
    s = client.post("/v1/agents/pg-keep/sessions", json={"draft": {"system_prompt": "new"}}).json()
    v = client.post(f"/v1/sessions/{s['id']}/save-version", json={}).json()
    assert v["max_run_cost_usd"] == 0.5 and v["approval_policy"]["rules"][0]["label"] == "git push"


def test_cancelled_runs_in_metrics():
    t = time.time()
    rows = [{"id": f"r{i}", "status": st, "created_at": t, "started_at": t, "ended_at": t + 1, "cost_usd": 0.0,
             "error": "Stopped by user" if st == "cancelled" else None}
            for i, st in enumerate(("succeeded", "failed", "cancelled"))]
    h = metrics.health(rows, [])
    assert (h["finished"], h["cancelled"], h["success_rate"]) == (3, 1, 0.5)  # stopped runs are not rated
    assert metrics.classify_run(rows[2]) == [{"category": "cancelled", "reason": "Stopped by user", "count": 1}]
    assert "playground" in metrics.SOURCES


def test_generate_assist_counts_toward_and_respects_the_budget(client, monkeypatch):
    """Assist calls are metered grader calls: they add to month-to-date spend and get 402 once it is used up."""
    from forge import budget

    monkeypatch.setenv("FORGE_GRADER_MODEL", "fake:grader")
    monkeypatch.setenv("FORGE_PRICES", '{"fake:grader": [1000, 1000]}')
    a = agent(client, "pg-assist", monthly_budget_usd=1.0)
    r = client.post("/v1/agents/pg-assist/sample-task", json={})
    assert r.status_code == 200, r.text
    assert budget.month_to_date_usd(a["id"]) > 0
    client.patch("/v1/agents/pg-assist", json={"monthly_budget_usd": 0})
    r = client.post("/v1/agents/pg-assist/sample-task", json={})
    assert r.status_code == 402 and "Monthly budget" in r.json()["detail"]
