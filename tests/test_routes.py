"""Every feature router is mounted before the SPA catch-all, and new run sources reach metrics and filters."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from forge import db, metrics


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_EXECUTOR", "local")
    monkeypatch.setenv("FORGE_SCHEDULER", "0")
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    from forge.server import app as app_module

    dist = tmp_path / "dist"  # a built web app, so the SPA catch-all is live
    dist.mkdir()
    (dist / "index.html").write_text("<html>SPA</html>")
    monkeypatch.setattr(app_module, "WEB_DIST", dist)
    with TestClient(app_module.app, client=("127.0.0.1", 5000)) as c:
        c.post("/v1/agents", json={"slug": "r1", "name": "R", "system_prompt": "s", "model": "fake:echo"})
        yield c


def test_every_router_is_mounted_before_the_spa_catch_all(client):
    """One endpoint of each router answers with its own response, never the SPA page or its v1 404."""
    checks = [
        ("get", "/v1/agents/r1/governance", None, 200, "state"),                      # governance
        ("post", "/v1/agents/r1/prune", {}, 400, "pruning needs"),                    # authoring
        ("post", "/v1/agent-drafts", {"description": ""}, 400, None),                 # authoring
        ("get", "/v1/agents/r1/metrics", None, 200, "summary"),                       # observability api
        ("get", "/v1/runs/r_missing/timeline", None, 404, "run not found"),           # observability api
        ("get", "/healthz", None, 200, "components"),                                 # observability ops
        ("post", "/v1/agents/r1/tools/test", {"tool": {"name": "t", "description": "d", "url": "https://x.test",
                                                       "parameters": {"type": "object"}}, "network": False},
         200, "is_error"),                                                            # tools
        ("get", "/v1/agents/r1/triggers", None, 200, None),                           # triggers builder
        ("post", "/v1/hooks/github/tr_missing", {}, 404, "trigger not found"),        # triggers hooks
    ]
    for method, path, body, status, needle in checks:
        r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == status, (path, r.status_code, r.text[:200])
        assert "SPA" not in r.text and r.json() != {"detail": "not found"}, path
        if needle:
            assert needle in r.text, (path, r.text[:200])
    m = client.get("/metrics")
    assert m.status_code == 200 and m.text.startswith("# HELP") and "forge_runs_awaiting_approval" in m.text
    assert "SPA" in client.get("/agents/r1").text  # the catch-all still serves the app


def test_trigger_and_agent_sources_are_production_metrics_and_filters(client):
    """Runs from triggers (schedule, github, slack) and agent__ tools count as production, filter by source and
    get Prometheus source labels."""
    a = db.get_agent_by_slug("r1")
    t = time.time()
    for src in ("schedule", "github", "slack", "agent", "eval"):
        r = db.create_run(a["id"], a["prod_version_id"], src, "x")
        db.update_run(r["id"], status="succeeded", started_at=t, ended_at=t + 1)
    assert set(metrics.SOURCES) >= {"schedule", "github", "slack", "agent"}
    m = client.get("/v1/agents/r1/metrics").json()
    assert m["summary"]["runs"] == 4  # every source but eval
    got = client.get("/v1/agents/r1/runs", params={"source": "schedule,github,slack"})
    assert got.headers["X-Total-Count"] == "3" and {x["source"] for x in got.json()} == {"schedule", "github", "slack"}
    text = client.get("/metrics").text
    for src in ("schedule", "github", "slack", "agent"):
        assert f'source="{src}"' in text, src
