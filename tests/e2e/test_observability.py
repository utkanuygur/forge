"""End-to-end observability: metrics, cost, failure categories, tool stats, run search, timelines, health check
and Prometheus metrics against a real server with Docker sandboxes and fake models (OBS1 to OBS7).

The server fixture sets FORGE_PRICES so fake:hello costs $1000 / $2000 per million input / output tokens
(a hello run uses 30 input and 15 output tokens, so it costs exactly $0.06) and fake:grader $100 / $500.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import httpx
import pytest

from tests.e2e.conftest import REPO, _free_port
from tests.e2e.helpers import poll, wait_improvement

pytestmark = pytest.mark.e2e

REMOTE = {"X-Forwarded-For": "203.0.113.9"}
HELLO_COST = (30 * 1000 + 15 * 2000) / 1e6


def run(api: httpx.Client, slug: str, input: str, version: int | None = None, **kw) -> dict:
    body = {"input": input, "wait": True, **({"version": version} if version else {})}
    r = api.post(f"/agents/{slug}/runs", json=body, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def new_version(api: httpx.Client, slug: str, **fields) -> int:
    r = api.post(f"/agents/{slug}/versions", json=fields)
    assert r.status_code == 200, r.text
    return r.json()["n"]


@pytest.fixture(scope="module")
def observed(api, request):
    """One agent with four versions and a run of each kind:
    v1 fake:hello (2 runs: thumbs up, thumbs down), v2 fake:hello without write_file (tool errors),
    v3 fake:error (model error), v4 fake:loop (turn limit timeout). Then v2 is promoted."""
    slug = f"obs-{uuid.uuid4().hex[:6]}"
    r = api.post("/agents", json={"slug": slug, "name": "Observed", "system_prompt": "You test.", "model": "fake:hello"})
    assert r.status_code == 200, r.text
    agent = r.json()
    up = run(api, slug, "write hello alpha-needle please")
    down = run(api, slug, "write hello again")
    api.post(f"/runs/{up['id']}/feedback", json={"rating": 1})
    api.post(f"/runs/{down['id']}/feedback", json={"rating": -1, "comment": "Too slow for this task"})
    v2 = new_version(api, slug, tools=["bash"], rationale="bash only")
    tool_err = run(api, slug, "hello without write access", version=v2)
    v3 = new_version(api, slug, model="fake:error")
    model_err = run(api, slug, "this model fails", version=v3)
    v4 = new_version(api, slug, model="fake:loop")
    looped = run(api, slug, "loop until the limit", version=v4)
    assert (up["status"], tool_err["status"], model_err["status"], looped["status"]) == (
        "succeeded", "succeeded", "failed", "timeout")
    assert api.post(f"/agents/{slug}/promote", json={"version": v2}).status_code == 200
    return {"agent": agent, "slug": slug, "up": up, "down": down, "tool_err": tool_err, "model_err": model_err,
            "looped": looped}


def test_agent_metrics_health_cost_failures_tools_and_promotion_marker(api, observed):
    """OBS1, OBS2, OBS4, OBS6: fake:hello, fake:error and fake:loop runs give the expected success rate, cost
    (from FORGE_PRICES), failure categories with example runs, tool statistics and a promotion marker."""
    slug = observed["slug"]
    m = api.get(f"/agents/{slug}/metrics", params={"days": 14}).json()
    s = m["summary"]
    assert (s["runs"], s["succeeded"], s["failed"], s["timeout"]) == (5, 3, 1, 1)
    assert s["success_rate"] == pytest.approx(3 / 5) and s["thumbs_up_rate"] == 0.5 and s["feedback_count"] == 2
    assert s["p50_ms"] > 0 and s["p95_ms"] >= s["p50_ms"]
    assert s["cost_usd"] == pytest.approx(3 * HELLO_COST)  # v1 x2 and v2 at fake:hello prices; error and loop are free
    assert sum(b["runs"] for b in m["series"]) == 5 and m["bucket"] == "day"

    rows = {v["version"]: v for v in m["versions"]}
    assert m["prod_version"] == 2 and m["previous_prod_version"] == 1
    assert rows[2]["is_prod"] and rows[1]["is_previous_prod"] and not rows[3]["is_prod"]
    assert rows[1]["health"]["runs"] == 2 and rows[1]["health"]["success_rate"] == 1.0
    assert rows[3]["health"]["success_rate"] == 0.0 and rows[3]["price"]["note"] == "self-hosted, not metered"
    assert rows[1]["price"]["kind"] == "metered" and rows[1]["price"]["input_per_mtok"] == 1000
    assert "delta_vs_previous_prod" in rows[2]
    assert [(p["version"], p["from_version"], p["source"]) for p in m["promotions"]] == [(1, None, "create"),
                                                                                          (2, 1, "human")]
    cost_rows = {c["version"]: c for c in m["cost"]["versions"]}
    assert cost_rows[1]["cost_usd"] == pytest.approx(2 * HELLO_COST) and cost_rows[4]["cost_usd"] == 0

    cats = {c["category"]: c for c in m["failures"]["all"]["categories"]}
    assert set(cats) == {"model_error", "timeout", "tool_errors", "negative_feedback"}
    assert cats["model_error"]["example_run_ids"] == [observed["model_err"]["id"]]
    assert cats["timeout"]["example_run_ids"] == [observed["looped"]["id"]]
    assert cats["tool_errors"]["count"] == 2  # write_file not allowed, then python hello.py fails
    reasons = {(r["category"], r["reason"]) for r in m["failures"]["all"]["reasons"]}
    assert ("negative_feedback", "Too slow for this task") in reasons
    assert ("timeout", "turn limit of 30 reached") in reasons
    assert ("model_error", "RuntimeError: fake model failure") in reasons
    by_version = {v["version"]: v for v in m["failures"]["versions"]}
    assert set(by_version) == {1, 2, 3, 4} and by_version[3]["categories"][0]["category"] == "model_error"

    tools = {t["name"]: t for t in m["tools"]["tools"]}
    assert tools["bash"]["calls"] == 1 + 1 + 1 + 30 and tools["bash"]["errors"] == 1
    assert tools["write_file"]["calls"] == 3 and tools["write_file"]["errors"] == 1
    assert tools["bash"]["p95_ms"] > 0 and tools["bash"]["total_ms"] > 0
    assert m["tools"]["slowest"] and m["tools"]["slowest"][0]["duration_ms"] >= m["tools"]["slowest"][-1]["duration_ms"]
    assert m["slowest_runs"][0]["duration_ms"] >= m["slowest_runs"][-1]["duration_ms"]


def test_run_cost_and_timeline_with_durations(api, observed):
    """OBS2, OBS3: a hello run costs $0.06 and its timeline has a span per model turn and tool call with
    recorded durations and tokens; model time plus tool time fits inside the run."""
    rid = observed["up"]["id"]
    run_json = api.get(f"/runs/{rid}").json()
    assert run_json["cost_usd"] == pytest.approx(HELLO_COST) and run_json["duration_ms"] > 0
    tl = api.get(f"/runs/{rid}/timeline").json()
    assert not tl["estimated"] and tl["cost_usd"] == pytest.approx(HELLO_COST)
    assert [(s["kind"], s["label"]) for s in tl["spans"]] == [
        ("model", "Model turn 1"), ("tool", "write_file"), ("model", "Model turn 2"), ("tool", "bash"),
        ("model", "Model turn 3")]
    assert all(s["duration_ms"] is not None and not s["estimated"] for s in tl["spans"])
    assert [s["tokens_in"] for s in tl["spans"] if s["kind"] == "model"] == [10, 10, 10]
    assert tl["tool_ms"] > 0 and tl["model_ms"] + tl["tool_ms"] <= tl["total_ms"] + 1
    starts = [s["start_ms"] for s in tl["spans"]]
    assert starts == sorted(starts)
    free = api.get(f"/runs/{observed['looped']['id']}").json()
    assert free["cost_usd"] == 0 and free["cost_note"] == "self-hosted, not metered"


def test_run_filters_search_and_sort(api, observed):
    """OBS5: status, version, source, rating, tool errors and text filters narrow the agent's runs; sort by
    duration and cost; X-Total-Count reports the match count; the global endpoint filters by agent."""
    slug = observed["slug"]

    def ids(**params) -> list[str]:
        r = api.get(f"/agents/{slug}/runs", params=params)
        assert r.status_code == 200, r.text
        assert int(r.headers["X-Total-Count"]) >= len(r.json())
        return [x["id"] for x in r.json()]

    o = observed
    assert len(ids()) == 5
    assert ids(status="failed") == [o["model_err"]["id"]]
    assert ids(status="failed,timeout") == [o["looped"]["id"], o["model_err"]["id"]]
    assert ids(version="2") == [o["tool_err"]["id"]]
    assert ids(rating="down") == [o["down"]["id"]] and ids(rating="up") == [o["up"]["id"]]
    assert len(ids(rating="none")) == 3
    assert ids(has_tool_errors="true") == [o["tool_err"]["id"]]
    assert ids(q="ALPHA-NEEDLE") == [o["up"]["id"]]
    assert ids(source="share") == [] and len(ids(source="api,ui")) == 5
    assert ids(sort="duration")[0] == o["looped"]["id"]
    assert set(ids(sort="cost")[:3]) == {o["up"]["id"], o["down"]["id"], o["tool_err"]["id"]}
    page = api.get(f"/agents/{slug}/runs", params={"limit": 2, "offset": 1})
    assert len(page.json()) == 2 and page.headers["X-Total-Count"] == "5"

    g = api.get("/runs", params={"agent": slug, "status": "timeout"})
    assert g.status_code == 200 and [x["id"] for x in g.json()] == [o["looped"]["id"]]
    assert g.json()[0]["agent_slug"] == slug and g.headers["X-Total-Count"] == "1"
    assert api.get("/runs", headers=REMOTE).status_code == 401
    assert api.get("/metrics/overview", headers=REMOTE).status_code == 401
    assert api.get(f"/agents/{slug}/metrics", headers=REMOTE).status_code == 401


def test_share_token_keeps_restricted_run_list(api, observed):
    """OBS5, FR5: a share-token caller sees only share runs even when asking for other sources, and cannot read
    metrics or timelines."""
    slug, token = observed["slug"], observed["agent"]["share_token"]
    share = {**REMOTE, "X-Share-Token": token}
    mine = api.post(f"/agents/{slug}/runs", json={"input": "from the share page", "wait": True}, headers=share).json()
    r = api.get(f"/agents/{slug}/runs", params={"source": "api"}, headers=share)
    assert r.status_code == 200 and [x["id"] for x in r.json()] == [mine["id"]]
    assert api.get(f"/runs/{mine['id']}/timeline", headers=share).status_code == 401
    assert api.get(f"/agents/{slug}/metrics", headers=share).status_code == 401


def test_overview_includes_agent_row_and_failure_reasons(api, observed):
    """OBS1: the dashboard overview counts the agent's runs, spend and failing runs and lists failure reasons."""
    o = api.get("/metrics/overview", params={"days": 1}).json()
    assert o["bucket"] == "hour"
    row = next(a for a in o["agents"] if a["slug"] == observed["slug"])
    assert row["health"]["runs"] >= 5 and row["failing_runs"] == 2 and sum(row["spark"]) == row["health"]["runs"]
    listed = api.get(f"/agents/{observed['slug']}/runs").json()  # includes the share page run
    assert row["health"]["cost_usd"] == pytest.approx(sum(x["cost_usd"] for x in listed)) and row["last_run_at"]
    assert o["kpis"]["runs"]["value"] >= 5 and set(o["kpis"]) == {"runs", "success_rate", "p95_ms", "cost_usd",
                                                                  "thumbs_up_rate"}
    assert any(r["agent"] == observed["slug"] for r in o["top_failure_reasons"])


def test_improvement_cycle_cost_and_promotion_marker(api, make_agent):
    """OBS2, OBS1, FR9: an improvement cycle's cost sums all k repeated eval runs plus judge and optimizer calls;
    promoting the candidate adds a promotion marker to the agent's metrics."""
    agent = make_agent("obsimp", model="fake:lessons")
    slug = agent["slug"]
    k = 3
    assert api.patch(f"/agents/{slug}", json={"eval_repeats": k}).status_code == 200
    for name, ctype, checker, split in (("job-alpha", "shell", "test -f MARKER.txt", "train"),
                                        ("job-judge", "judge", "The work is complete.", "holdout")):
        assert api.post(f"/agents/{slug}/evals", json={"name": name, "input": f"Do {name}.", "checker_type": ctype,
                                                       "checker": checker, "split": split}).status_code == 200
    im = api.post(f"/agents/{slug}/improve", json={}).json()
    done = wait_improvement(api, im["id"])
    assert done["status"] == "eligible", done
    m = api.get(f"/agents/{slug}/metrics").json()
    cyc = next(c for c in m["cost"]["improvements"] if c["id"] == im["id"])
    # 2 cases x 2 versions x k runs; one judge call per run of the judge case (2 versions x k) plus the optimizer
    assert cyc["eval_runs"] == 4 * k and cyc["grader_calls"] == 2 * k + 1
    assert cyc["judge_cost_usd"] == pytest.approx(2 * k * (50 * 100 + 10 * 500) / 1e6)
    assert cyc["optimizer_cost_usd"] == pytest.approx((200 * 100 + 40 * 500) / 1e6)
    # base: 2k one-turn runs (10 in, 5 out); candidate: 2k two-turn runs (20 in, 10 out), at $1000 / $2000
    assert cyc["eval_cost_usd"] == pytest.approx(k * ((2 * 0.02) + (2 * 0.04)))
    assert cyc["total_cost_usd"] == pytest.approx(cyc["eval_cost_usd"] + cyc["judge_cost_usd"]
                                                  + cyc["optimizer_cost_usd"])
    assert m["summary"]["runs"] == 0  # eval runs are not production usage
    assert api.get(f"/agents/{slug}/metrics", params={"include_eval": "true"}).json()["summary"]["runs"] == 4 * k
    evals = {v["version"]: v["eval"] for v in m["versions"]}
    assert evals[1]["train"]["passed"] == 0 and evals[2]["train"]["passed"] == 1 and evals[2]["holdout"]["pass_rate"] == 1

    assert api.post(f"/agents/{slug}/promote", json={"version": 2}).status_code == 200
    m2 = api.get(f"/agents/{slug}/metrics").json()
    assert [(p["version"], p["source"]) for p in m2["promotions"]][-1] == (2, "human")
    assert m2["prod_version"] == 2 and m2["previous_prod_version"] == 1
    text = httpx.get(api.base_url.join("/metrics")).text
    assert 'forge_improvements_total{status="eligible"}' in text


def test_healthz_reports_database_and_docker(api):
    """OBS7: /healthz needs no auth and reports database and Docker as ok with status 200."""
    r = httpx.get(api.base_url.join("/healthz"), headers=REMOTE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["components"]["database"]["status"] == "ok"
    assert body["components"]["docker"]["status"] == "ok"


def test_version_needs_no_auth_and_matches_healthz(api):
    """GET /v1/version answers a remote caller without a key: a dev server from this checkout reports channel
    dev and the checkout's short commit, and /healthz carries the same object."""
    r = httpx.get(api.base_url.join("/v1/version"), headers=REMOTE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"version", "commit", "built_at", "channel"}
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    assert body["channel"] == "dev" and body["commit"] == head and body["built_at"] is None
    assert body["version"] is None or body["version"].endswith("+dev")
    assert httpx.get(api.base_url.join("/healthz"), headers=REMOTE).json()["build"] == body


def test_healthz_503_when_sandbox_image_missing(tmp_path):
    """OBS7: a server whose Docker check fails (sandbox image missing) answers /healthz with 503."""
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("FORGE_")}
    env.update({"FORGE_DATA_DIR": str(tmp_path / "data"), "FORGE_EXECUTOR": "docker",
                "FORGE_SANDBOX_IMAGE": f"forge-sandbox-missing-{uuid.uuid4().hex[:6]}",
                "PYTHONPATH": str(REPO), "FORGE_LOG_JSON": "0"})
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "forge.server.app:app", "--host", "127.0.0.1",
                             "--port", str(port), "--log-level", "warning"], cwd=tmp_path, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        def get() -> httpx.Response | None:
            try:
                return httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=15)
            except httpx.HTTPError:
                return None

        r = poll(get, timeout=30, interval=0.2, what="second server")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "error" and body["components"]["database"]["status"] == "ok"
        assert body["components"]["docker"]["status"] == "error" and "not found" in body["components"]["docker"]["detail"]
    finally:
        proc.terminate()
        proc.wait(timeout=20)


def test_prometheus_metrics_content_and_localhost_restriction(api, observed):
    """OBS7: /metrics exposes run, duration, token, cost, tool, active run and improvement metrics in
    Prometheus text format, and refuses remote clients."""
    url = api.base_url.join("/metrics")
    r = httpx.get(url)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    text, slug = r.text, observed["slug"]
    assert f'forge_runs_total{{agent="{slug}",status="succeeded",source="ui"}}' in text
    assert f'forge_runs_total{{agent="{slug}",status="timeout",source="ui"}} 1' in text
    assert f'forge_run_duration_seconds_bucket{{agent="{slug}",le="+Inf"}}' in text
    assert f'forge_tokens_total{{agent="{slug}",direction="input"}}' in text
    assert f'forge_cost_usd_total{{agent="{slug}"}}' in text
    assert 'forge_tool_calls_total{tool="bash",error="true"}' in text
    assert "# TYPE forge_active_runs gauge" in text and "# TYPE forge_run_duration_seconds histogram" in text
    assert "# TYPE forge_improvements_total counter" in text
    assert httpx.get(url, headers=REMOTE).status_code == 401
