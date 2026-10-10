"""End-to-end governance tests: approval prompts and spending caps against a real server, Docker sandboxes
and fake models. This module starts its own server so it can set FORGE_PRICES (fake:loop costs 0.02 USD
per turn: 10 input tokens at 1000 USD and 5 output tokens at 2000 USD per million)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx
import pytest

from tests.conftest import docker_unavailable
from tests.e2e.conftest import REPO, Server, _free_port, sandbox_containers
from tests.e2e.helpers import assert_gapless, events, poll, tar_members, wait_improvement

pytestmark = pytest.mark.e2e

PRICES = {"fake:loop": [1000, 2000]}
DONE = {"succeeded", "failed", "timeout", "budget_exceeded"}
RM_ASK = {"rules": [{"tool": "bash", "pattern": r"\brm\s+-\w*([rR]\w*f|f\w*[rR])", "mode": "ask", "label": "rm -rf"}]}
SEED = {"build/out.txt": "stale build output\n", "README.md": "keep\n"}


@pytest.fixture(scope="module")
def gov(tmp_path_factory: pytest.TempPathFactory) -> Iterator[httpx.Client]:
    if why := docker_unavailable():
        pytest.skip(why)
    root = tmp_path_factory.mktemp("forge-gov")
    log = root / "server.log"
    port = _free_port()
    env = {k: v for k, v in os.environ.items()
           if (not k.startswith("FORGE_") or k == "FORGE_SANDBOX_IMAGE") and k != "ANTHROPIC_API_KEY"}
    env.update({"FORGE_DATA_DIR": str(root / "data"), "FORGE_EXECUTOR": "docker", "FORGE_DEFAULT_MODEL": "fake:hello",
                "FORGE_GRADER_MODEL": "fake:grader", "FORGE_PRICES": json.dumps(PRICES), "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": os.pathsep.join(p for p in (str(REPO), os.environ.get("PYTHONPATH", "")) if p)})
    before = sandbox_containers()
    logf = log.open("w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "forge.server.app:app", "--host", "127.0.0.1",
                             "--port", str(port), "--log-level", "warning"],
                            cwd=root, env=env, stdout=logf, stderr=subprocess.STDOUT)
    srv = Server(f"http://127.0.0.1:{port}", root / "data", log)

    def healthy() -> bool | None:
        if proc.poll() is not None:
            raise AssertionError(f"server exited:\n{srv.log_tail()}")
        try:
            return httpx.get(f"{srv.url}/v1/templates", timeout=2).status_code == 200 or None
        except httpx.HTTPError:
            return None

    try:
        poll(healthy, timeout=30, interval=0.1, what="governance server health")
        with httpx.Client(base_url=srv.url + "/v1", timeout=120) as c:
            yield c
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        logf.close()
    poll(lambda: not (sandbox_containers() - before) or None, timeout=20, interval=0.5,
         what="governance sandbox containers to exit")


def agent(c: httpx.Client, model: str = "fake:risky", **kw: Any) -> dict:
    r = c.post("/agents", json={"slug": f"gov-{uuid.uuid4().hex[:6]}", "name": "gov", "system_prompt": "test",
                                "model": model, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def wait_done(c: httpx.Client, run_id: str, timeout: float = 90) -> dict:
    return poll(lambda: (r := c.get(f"/runs/{run_id}").json())["status"] in DONE and r or None, timeout=timeout,
                what=f"run {run_id} to finish")


def wait_approval(c: httpx.Client, run_id: str) -> dict:
    def pending() -> dict | None:
        evs = c.get(f"/runs/{run_id}/events.json").json()
        return next((e["payload"] for e in evs if e["type"] == "approval_requested"), None)

    return poll(pending, timeout=60, what=f"approval request on {run_id}")


class Receiver:
    def __init__(self) -> None:
        self.bodies: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                outer.bodies.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a: Any) -> None:
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/hook"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


def test_approve_executes_the_paused_call_and_notifies_the_webhook(gov):
    """GOV2, GOV9: an `ask` match pauses the run (awaiting_approval, sandbox kept alive); the webhook gets the
    pending approval; a share-link caller is refused; the builder approves and the call runs."""
    a = agent(gov, approval_policy=RM_ASK, seed_files=SEED)
    hook = Receiver()
    run = gov.post(f"/agents/{a['slug']}/runs", json={"input": "clean the build", "callback_url": hook.url}).json()
    req = wait_approval(gov, run["id"])
    assert req["name"] == "bash" and req["args"] == {"cmd": "rm -rf build"} and req["rule"]["label"] == "rm -rf"
    assert poll(lambda: gov.get(f"/runs/{run['id']}").json()["status"] == "awaiting_approval", timeout=10)
    g = gov.get(f"/agents/{a['slug']}/governance").json()
    assert g["pending_approvals"] == 1 and g["awaiting_runs"] == [run["id"]]
    assert gov.get(f"/agents/{a['slug']}").json()["pending_approvals"] == 1
    note = poll(lambda: next((b for b in hook.bodies if "approval" in b), None), timeout=15, what="approval webhook")
    assert note["status"] == "awaiting_approval" and note["approval"]["id"] == req["id"]

    url = f"/runs/{run['id']}/approvals/{req['id']}"
    assert gov.post(url, json={"decision": "approve"}, headers={"X-Share-Token": a["share_token"]}).status_code == 403
    r = gov.post(url, json={"decision": "approve"}, headers={"Authorization": f"Bearer {a['api_key']}"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["resolved_by"] == "api"
    assert gov.post(url, json={"decision": "deny"}).status_code == 409

    done = wait_done(gov, run["id"])
    assert done["status"] == "succeeded" and done["output"] == "Removed the build directory."
    evs = events(gov, run["id"])
    assert_gapless(evs)
    assert [e["type"] for e in evs] == ["run_started", "tool_call", "approval_requested", "approval_resolved",
                                        "tool_result", "assistant", "run_finished"]
    assert evs[3]["payload"]["decision"] == "approve" and not evs[4]["payload"]["is_error"]
    files = tar_members(gov.get(f"/runs/{run['id']}/artifacts").content)
    assert "build/out.txt" not in files and "README.md" in files
    assert gov.get(f"/runs/{run['id']}/approvals").json()[0]["status"] == "approved"
    poll(lambda: any(b.get("status") == "succeeded" for b in hook.bodies) or None, timeout=15, what="final webhook")


def test_deny_with_note_returns_error_and_the_run_continues(gov):
    """GOV3: deny returns `Denied by reviewer: <note>` as an error tool result; the model adapts and the run
    succeeds without executing the call."""
    a = agent(gov, approval_policy=RM_ASK, seed_files=SEED)
    run = gov.post(f"/agents/{a['slug']}/runs", json={"input": "clean the build"}).json()
    req = wait_approval(gov, run["id"])
    r = gov.post(f"/runs/{run['id']}/approvals/{req['id']}", json={"decision": "deny", "note": "keep the build"})
    assert r.status_code == 200 and r.json()["status"] == "denied"
    done = wait_done(gov, run["id"])
    assert done["status"] == "succeeded" and "Denied by reviewer: keep the build" in done["output"]
    result = next(e["payload"] for e in events(gov, run["id"]) if e["type"] == "tool_result")
    assert result["is_error"] and result["output"].startswith("Denied by reviewer: keep the build")
    assert "build/out.txt" in tar_members(gov.get(f"/runs/{run['id']}/artifacts").content)


def test_timeout_auto_denies(gov):
    """GOV4: with no decision before the agent's approval timeout the call is denied automatically."""
    a = agent(gov, approval_policy={**RM_ASK, "timeout_seconds": 2}, seed_files=SEED)
    run = gov.post(f"/agents/{a['slug']}/runs", json={"input": "clean the build", "wait": True}).json()
    assert run["status"] == "succeeded"
    evs = events(gov, run["id"])
    resolved = next(e["payload"] for e in evs if e["type"] == "approval_resolved")
    assert resolved["decision"] == "deny" and resolved["by"] == "timeout"
    assert gov.get(f"/runs/{run['id']}/approvals").json()[0]["status"] == "expired"
    assert "build/out.txt" in tar_members(gov.get(f"/runs/{run['id']}/artifacts").content)


def test_deny_rule_refuses_without_prompting(gov):
    """GOV5: a `deny` rule never executes the call and creates no approval."""
    a = agent(gov, approval_policy={"rules": [{"tool": "bash", "pattern": "rm ", "mode": "deny"}]}, seed_files=SEED)
    run = gov.post(f"/agents/{a['slug']}/runs", json={"input": "clean", "wait": True}).json()
    assert run["status"] == "succeeded" and "Denied by policy" in run["output"]
    assert not any(e["type"].startswith("approval") for e in events(gov, run["id"]))
    assert gov.get(f"/runs/{run['id']}/approvals").json() == []


def test_per_run_cap_ends_budget_exceeded(gov):
    """GOV6, GOV8: with FORGE_PRICES pricing fake:loop, a run over its cap ends `budget_exceeded` with an error
    naming the cap and the spend, after an 80% budget_warning."""
    a = agent(gov, model="fake:loop", max_run_cost_usd=0.05)
    run = gov.post(f"/agents/{a['slug']}/runs", json={"input": "loop", "wait": True}).json()
    assert run["status"] == "budget_exceeded" and run["turns"] == 3
    assert run["cost_usd"] == pytest.approx(0.06)
    assert run["error"] == "Run stopped: per-run spending cap of $0.05 exceeded ($0.06 spent)."
    evs = events(gov, run["id"])
    assert_gapless(evs)
    assert [e["payload"]["scope"] for e in evs if e["type"] == "budget_warning"] == ["run"]
    assert any(e["type"] == "error" and "$0.05" in e["payload"]["message"] for e in evs)
    assert evs[-1]["payload"]["status"] == "budget_exceeded"


def test_monthly_budget_refuses_new_runs_with_402(gov):
    """GOV7, GOV8: spend reaching the monthly budget warns at 80%; afterwards new runs and evals get HTTP 402."""
    a = agent(gov, model="fake:loop", max_run_cost_usd=0.03, monthly_budget_usd=0.04)
    run = gov.post(f"/agents/{a['slug']}/runs", json={"input": "loop", "wait": True}).json()
    assert run["status"] == "budget_exceeded" and run["cost_usd"] == pytest.approx(0.04)
    assert "month" in [e["payload"]["scope"] for e in events(gov, run["id"]) if e["type"] == "budget_warning"]
    view = gov.get(f"/agents/{a['slug']}").json()
    assert view["month_to_date_usd"] == pytest.approx(0.04) and view["budget_state"] == "exceeded"
    r = gov.post(f"/agents/{a['slug']}/runs", json={"input": "again"}, headers={"Authorization": f"Bearer {a['api_key']}"})
    assert r.status_code == 402 and "Monthly budget of $0.04 reached" in r.json()["detail"]
    assert gov.post(f"/agents/{a['slug']}/evals/run", json={}).status_code == 402
    assert gov.patch(f"/agents/{a['slug']}", json={"monthly_budget_usd": 1.0}).status_code == 200
    assert gov.post(f"/agents/{a['slug']}/runs", json={"input": "ok now"}).status_code == 200


def test_improvement_stops_when_the_budget_runs_out(gov):
    """GOV7: eval runs count toward the budget; an improvement cycle that hits it stops starting eval runs
    and ends `failed` with the budget reason."""
    a = agent(gov, model="fake:loop", max_run_cost_usd=0.03, monthly_budget_usd=0.04)
    case = {"name": "c1", "input": "x", "checker_type": "shell", "checker": "false", "split": "train"}
    assert gov.post(f"/agents/{a['slug']}/evals", json=case).status_code == 200
    im = gov.post(f"/agents/{a['slug']}/improve", json={}).json()
    done = wait_improvement(gov, im["id"])
    assert done["status"] == "failed" and "Monthly budget of $0.04 reached" in (done["error"] or ""), done
    runs = gov.get(f"/agents/{a['slug']}/runs").json()
    assert len(runs) == 1 and runs[0]["source"] == "eval" and runs[0]["cost_usd"] == pytest.approx(0.04)
