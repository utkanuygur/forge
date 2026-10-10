"""Governance unit tests: approval policy matching, the approval pause, spending caps and pricing (GOV1..GOV9)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge import budget, db, governance, metrics, pricing
from forge.loop import run_agent
from forge.policy import PRESETS, Policy, glob_match, match, parse_policy
from forge.providers import ScenarioProvider
from forge.types import AgentVersion, Event, ExecResult


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("FORGE_PRICES", raising=False)


# ---------------------------------------------------------------- policy matching (GOV1)


def pol(*rules: dict, **kw: Any) -> Policy:
    return parse_policy({"rules": list(rules), **kw})


def test_bash_regex_rules_and_presets_match_risky_commands():
    """GOV1: bash rules are regexes searched in `cmd`; presets catch rm -rf, git push, curl/wget, installs."""
    p = pol(*PRESETS["rm_rf"], *PRESETS["git_push"], *PRESETS["curl_wget"], *PRESETS["package_installs"])
    def hit(cmd: str) -> str | None:
        m = match(p, "bash", {"cmd": cmd})
        return m[1].label if m else None
    assert hit("rm -rf build") == "rm -rf" and hit("rm -fr /tmp/x") == "rm -rf" and hit("sudo rm -Rf /") == "rm -rf"
    assert hit("rm notes.txt") is None and hit("ls -la") is None
    assert hit("git push origin main") == "git push" and hit("git pull") is None
    assert hit("curl -s https://x") == "curl or wget" and hit("wget x") == "curl or wget"
    for cmd in ("pip install requests", "uv add httpx", "npm install", "npm i left-pad", "apt-get install jq",
                "cargo add serde", "yarn add react"):
        assert hit(cmd) == "package installs", cmd
    assert hit("pip list") is None and hit("npm test") is None


def test_file_globs_star_tool_and_deny_precedence():
    """GOV1: file rules are path globs; a bare glob matches any directory; `**` crosses directories;
    a matching deny wins over an earlier ask; no pattern means every call to that tool."""
    assert glob_match("*.env", ".env") and glob_match("*.env", "config/prod.env")
    assert glob_match("migrations/**", "migrations/001.sql") and glob_match("migrations/**", "migrations/a/b.sql")
    assert not glob_match("migrations/**", "src/migrations/x.sql") and not glob_match("src/*.py", "src/a/b.py")
    assert glob_match("**/secrets/*", "a/b/secrets/k") and glob_match("./x.txt", "x.txt")

    p = pol({"tool": "write_file", "pattern": "*.env", "mode": "ask"},
            {"tool": "write_file", "pattern": "prod.env", "mode": "deny"},
            {"tool": "edit_file", "mode": "ask"})
    assert match(p, "write_file", {"path": "dev.env", "content": ""})[1].mode == "ask"
    idx, rule = match(p, "write_file", {"path": "conf/prod.env", "content": ""})
    assert (idx, rule.mode) == (1, "deny")
    assert match(p, "write_file", {"path": "README.md", "content": ""}) is None
    assert match(p, "edit_file", {"path": "anything.py", "old": "a", "new": "b"})[0] == 2
    assert match(p, "bash", {"cmd": "cat .env"}) is None
    assert match(pol({"tool": "*", "mode": "deny"}), "read_file", {"path": "a"})[1].mode == "deny"


def test_policy_validation_and_defaults():
    """GOV1: the empty policy matches nothing; bad regexes, unknown tools and `*` with a pattern are rejected;
    the default approval timeout is 15 minutes."""
    assert parse_policy(None).rules == [] and parse_policy({}).timeout_seconds == 900
    assert match(parse_policy({}), "bash", {"cmd": "rm -rf /"}) is None
    for bad in ({"rules": [{"tool": "bash", "pattern": "(unclosed"}]}, {"rules": [{"tool": "nope"}]},
                {"rules": [{"tool": "*", "pattern": "x"}]}, {"rules": [{"tool": "bash", "mode": "maybe"}]},
                {"timeout_seconds": 0}):
        with pytest.raises(ValueError):
            parse_policy(bad)


# ---------------------------------------------------------------- pricing and budget math (GOV6)


def test_budget_uses_the_one_price_source(monkeypatch):
    """GOV6: budgets, the stored run cost and metrics share forge.pricing.cost: no fallback table, FORGE_PRICES
    respected, fake and openai_compat free, unknown models None (the stored cost stays null)."""
    assert not hasattr(budget, "FALLBACK_PRICES") and not hasattr(budget, "estimate_cost_usd")
    assert pricing.cost("anthropic:claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)
    assert pricing.cost("fake:hello", 10**9, 10**9) == 0.0
    assert pricing.cost("anthropic:some-unknown-model", 1, 1) is None
    monkeypatch.setenv("FORGE_PRICES", '{"fake:loop": [1000, 2000], "claude-opus-5-5": {"input": 1, "output": 1}}')
    assert pricing.cost("fake:loop", 10, 5) == pytest.approx(0.02)
    assert pricing.cost("anthropic:claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(2.0)
    agent, v, run = _agent_and_run(model="anthropic:some-unknown-model")
    assert db.get_run(run["id"])["cost_usd"] is None  # unknown price: null, never a misleading 0
    _, _, priced = _agent_and_run(model="fake:loop")
    assert db.get_run(priced["id"])["cost_usd"] == 0.0


def _agent_and_run(model: str = "fake:risky", policy: dict | None = None, cap: float | None = None,
                   monthly: float | None = None) -> tuple[dict, dict, dict]:
    agent, _ = db.create_agent(f"a{time.time_ns()}", "A")
    v = db.create_version(agent["id"], system_prompt="s", model=model, approval_policy=policy or {},
                          max_run_cost_usd=cap)
    agent = db.update_agent(agent["id"], prod_version_id=v["id"], monthly_budget_usd=monthly)
    run = db.create_run(agent["id"], v["id"], "api", "go", status="running")
    return agent, v, run


def test_month_to_date_spend_and_budget_check(monkeypatch):
    """GOV7: month-to-date spend sums run costs since the first of the month (UTC); at or over the
    budget ensure_budget raises; 80% is the warning state."""
    assert budget.month_start(1_760_000_000) == 1_759_276_800  # 2025-10-01T00:00:00Z
    agent, _, run = _agent_and_run(monthly=1.0)
    old = db.create_run(agent["id"], agent["prod_version_id"], "api", "old")
    db.update_run(old["id"], cost_usd=50.0, created_at=budget.month_start() - 10)  # last month: not counted
    db.update_run(run["id"], cost_usd=0.85)
    st = budget.budget_status(agent)
    assert st["state"] == "warning" and st["month_to_date_usd"] == pytest.approx(0.85)
    budget.ensure_budget(agent)
    db.update_run(run["id"], cost_usd=1.0)
    with pytest.raises(budget.BudgetExceeded, match=r"Monthly budget of \$1.00 reached"):
        budget.ensure_budget(agent)
    assert budget.budget_status(db.update_agent(agent["id"], monthly_budget_usd=None))["state"] == "none"


# ---------------------------------------------------------------- the approval pause in the loop (GOV2..GOV5)


class Exec:
    def __init__(self) -> None:
        self.cmds: list[str] = []

    async def exec(self, cmd: str, timeout: int = 120) -> ExecResult:
        self.cmds.append(cmd)
        return ExecResult(exit_code=0, stdout="", stderr="")


async def _run(version: dict, agent: dict, run: dict, registry: governance.ApprovalRegistry,
               on_request=None, source: str | None = None) -> tuple[Any, list[Event], Exec]:
    events: list[Event] = []
    ex = Exec()

    async def emit(e: Event) -> None:
        events.append(e)
        if e.type == "approval_requested" and on_request:
            on_request(e)

    hooks = governance.RunGovernor(run["id"], agent, version, registry, source=source)
    v = AgentVersion(**{k: version[k] for k in AgentVersion.model_fields if k in version})
    res = await run_agent(v, "clean up", run_id=run["id"], provider=ScenarioProvider(version["model"][5:]),
                          executor=ex, emit=emit, hooks=hooks)  # type: ignore[arg-type]
    return res, events, ex


RM_ASK = {"rules": PRESETS["rm_rf"]}


async def test_timeout_auto_denies_and_the_run_continues():
    """GOV4: with no decision before the policy timeout the call is denied, never executed, the model gets
    an is_error result and the run continues; the approval is stored as expired."""
    agent, v, run = _agent_and_run(policy={**RM_ASK, "timeout_seconds": 1})
    reg = governance.ApprovalRegistry()
    seen: list[str] = []
    res, evs, ex = await _run(v, agent, run, reg,
                              on_request=lambda e: seen.append(db.get_run(run["id"])["status"]))
    types_ = [e.type for e in evs]
    assert types_ == ["run_started", "tool_call", "approval_requested", "approval_resolved", "tool_result",
                      "assistant", "run_finished"]
    req, resolved, result = evs[2].payload, evs[3].payload, evs[4].payload
    assert req["name"] == "bash" and req["args"] == {"cmd": "rm -rf build"} and req["rule"]["label"] == "rm -rf"
    assert req["expires_at"] > time.time() - 5 and req["tool_call_id"] == evs[1].payload["id"]
    assert resolved == {"id": req["id"], "tool_call_id": req["tool_call_id"], "decision": "deny",
                        "note": "no decision within 1 seconds", "by": "timeout"}
    assert result["is_error"] and result["output"].startswith("Denied by reviewer: no decision within 1 seconds")
    assert ex.cmds == [] and res.status == "succeeded" and "not allowed" in res.output
    ap = governance.get_approval(req["id"])
    assert ap["status"] == "expired" and ap["resolved_by"] == "timeout"
    assert db.get_run(run["id"])["status"] == "running"  # restored after the pause (finalize sets the end state)
    await asyncio.sleep(0)


async def test_approve_executes_and_deny_returns_reviewer_note():
    """GOV2, GOV3: approve runs the call; deny returns `Denied by reviewer: <note>` and the run continues.
    The run is `awaiting_approval` while paused."""
    for decision, note in (("approve", ""), ("deny", "not on prod")):
        agent, v, run = _agent_and_run(policy=RM_ASK)
        reg = governance.ApprovalRegistry()
        statuses: list[str] = []

        def on_req(e: Event, reg=reg, decision=decision, note=note, run=run) -> None:
            async def later() -> None:
                await asyncio.sleep(0.05)
                statuses.append(db.get_run(run["id"])["status"])
                assert reg.is_pending(e.payload["id"])
                assert reg.resolve(e.payload["id"], decision, note, "ui")
                assert not reg.resolve(e.payload["id"], "approve")  # second decision is refused
            asyncio.get_running_loop().create_task(later())

        res, evs, ex = await _run(v, agent, run, reg, on_request=on_req)
        assert statuses == ["awaiting_approval"]
        tool_result = next(e.payload for e in evs if e.type == "tool_result")
        resolved = next(e.payload for e in evs if e.type == "approval_resolved")
        assert resolved["decision"] == decision and resolved["by"] == "ui"
        if decision == "approve":
            assert ex.cmds == ["rm -rf build"] and not tool_result["is_error"]
            assert res.output == "Removed the build directory."
        else:
            assert ex.cmds == [] and tool_result["output"].startswith("Denied by reviewer: not on prod")
            assert tool_result["is_error"] and res.status == "succeeded"


async def test_deny_rule_never_executes_and_no_approval_is_created():
    """GOV5: a deny rule returns an error result immediately, with no approval prompt."""
    agent, v, run = _agent_and_run(policy={"rules": [{"tool": "bash", "pattern": "rm", "mode": "deny"}]})
    res, evs, ex = await _run(v, agent, run, governance.ApprovalRegistry())
    assert ex.cmds == [] and not any(e.type.startswith("approval") for e in evs)
    out = next(e.payload for e in evs if e.type == "tool_result")
    assert out["is_error"] and out["output"].startswith("Denied by policy: bash matching /rm/")
    assert governance.list_approvals(run["id"]) == [] and res.status == "succeeded"


async def test_per_run_cap_ends_budget_exceeded_with_warning(monkeypatch):
    """GOV6, GOV8: cumulative cost is checked after each model turn; at 80% a budget_warning is emitted and
    above the cap the run ends `budget_exceeded` with an error naming the cap and the spend."""
    monkeypatch.setenv("FORGE_PRICES", '{"fake:loop": [1000, 2000]}')  # 0.02 USD per fake turn
    agent, v, run = _agent_and_run(model="fake:loop", cap=0.05)
    res, evs, ex = await _run(v, agent, run, governance.ApprovalRegistry())
    assert res.status == "budget_exceeded" and res.turns == 3 and len(ex.cmds) == 2
    warn = [e.payload for e in evs if e.type == "budget_warning"]
    assert warn == [{"scope": "run", "limit_usd": 0.05, "spent_usd": 0.04, "fraction": 0.8}]
    err = next(e.payload["message"] for e in evs if e.type == "error")
    assert err == "Run stopped: per-run spending cap of $0.05 exceeded ($0.06 spent)." == res.error
    assert evs[-1].payload["status"] == "budget_exceeded"
    assert db.get_run(run["id"])["cost_usd"] == pytest.approx(0.06)


async def test_monthly_warning_event(monkeypatch):
    """GOV8: when month-to-date spend reaches 80% of the monthly budget during a run, one budget_warning
    with scope `month` is emitted."""
    monkeypatch.setenv("FORGE_PRICES", '{"fake:loop": [1000, 2000]}')
    agent, v, run = _agent_and_run(model="fake:loop", cap=0.05, monthly=0.05)
    _, evs, _ = await _run(v, agent, run, governance.ApprovalRegistry())
    month = [e.payload for e in evs if e.type == "budget_warning" and e.payload["scope"] == "month"]
    assert month == [{"scope": "month", "limit_usd": 0.05, "spent_usd": 0.04, "fraction": 0.8}]


# ---------------------------------------------------------------- HTTP (GOV7, GOV9)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FORGE_EXECUTOR", "local")
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    from forge.server.app import app

    with TestClient(app, client=("127.0.0.1", 5000)) as c:
        yield c


def test_http_policy_validation_budget_402_and_fields(client):
    """GOV1, GOV7: invalid policies are a 400; caps and policy carry into new versions; a spent monthly
    budget refuses runs, evals and improvement with 402; agent and run views expose spend."""
    bad = client.post("/v1/agents", json={"slug": "g1", "name": "g", "system_prompt": "s", "model": "fake:echo",
                                          "approval_policy": {"rules": [{"tool": "bash", "pattern": "("}]}})
    assert bad.status_code == 400 and "invalid approval_policy" in bad.json()["detail"]
    a = client.post("/v1/agents", json={"slug": "g1", "name": "g", "system_prompt": "s", "model": "fake:echo",
                                        "approval_policy": RM_ASK, "max_run_cost_usd": 0.5}).json()
    assert a["monthly_budget_usd"] is None and a["pending_approvals"] == 0 and a["budget_state"] == "none"
    v2 = client.post("/v1/agents/g1/versions", json={"rationale": "x"}).json()
    assert v2["max_run_cost_usd"] == 0.5 and v2["approval_policy"]["rules"][0]["label"] == "rm -rf"
    v3 = client.post("/v1/agents/g1/versions", json={"max_run_cost_usd": None}).json()
    assert v3["max_run_cost_usd"] is None and v3["approval_policy"]["timeout_seconds"] == 900

    run = client.post("/v1/agents/g1/runs", json={"input": "hi", "wait": True}).json()
    assert run["status"] == "succeeded" and run["cost_usd"] == 0.0

    assert client.patch("/v1/agents/g1", json={"monthly_budget_usd": 0}).json()["budget_state"] == "exceeded"
    for method, path, body in (("post", "/v1/agents/g1/runs", {"input": "hi"}), ("post", "/v1/agents/g1/evals/run", {}),
                               ("post", "/v1/agents/g1/improve", {})):
        r = getattr(client, method)(path, json=body)
        assert r.status_code == 402 and "Monthly budget of $0.00 reached" in r.json()["detail"], (path, r.text)
    gov = client.get("/v1/agents/g1/governance").json()
    assert gov["state"] == "exceeded" and gov["awaiting_runs"] == []
    assert client.patch("/v1/agents/g1", json={"monthly_budget_usd": None}).json()["monthly_budget_usd"] is None
    assert client.post("/v1/agents/g1/runs", json={"input": "hi", "wait": True}).json()["status"] == "succeeded"


def test_http_resolve_rules_share_forbidden_and_restart_interrupts(client):
    """GOV9: share-link callers cannot resolve (403); unknown approvals 404; a non-pending approval 409;
    server restart marks awaiting runs failed (interrupted) and pending approvals expired."""
    a = client.post("/v1/agents", json={"slug": "g2", "name": "g", "system_prompt": "s", "model": "fake:echo"}).json()
    agent = db.get_agent(a["id"])
    run = db.create_run(a["id"], agent["prod_version_id"], "share", "x", status="awaiting_approval")
    from forge.types import ToolCall

    ap = governance.create_approval(run["id"], ToolCall(id="c1", name="bash", args={"cmd": "rm -rf b"}), {}, {}, 60)
    url = f"/v1/runs/{run['id']}/approvals/{ap['id']}"
    r = client.post(url, json={"decision": "approve"}, headers={"X-Share-Token": a["share_token"]})
    assert r.status_code == 403
    assert client.post(f"/v1/runs/{run['id']}/approvals/ap_nope", json={"decision": "deny"}).status_code == 404
    assert client.post(url, json={"decision": "maybe"}).status_code == 422
    assert client.post(url, json={"decision": "approve"}).status_code == 409  # no live waiter in this process
    listed = client.get(f"/v1/runs/{run['id']}/approvals", headers={"X-Share-Token": a["share_token"]}).json()
    assert [x["id"] for x in listed] == [ap["id"]] and listed[0]["status"] == "pending"
    assert client.get("/v1/agents/g2").json()["pending_approvals"] == 1
    other = db.create_run(a["id"], agent["prod_version_id"], "api", "y")
    r = client.get(f"/v1/runs/{other['id']}/approvals", headers={"X-Share-Token": a["share_token"]})
    assert r.status_code == 404  # a share token only sees share runs

    assert db.fail_interrupted_runs() >= 1
    r2 = db.get_run(run["id"])
    assert (r2["status"], r2["error"]) == ("failed", "interrupted")
    assert governance.get_approval(ap["id"])["status"] == "expired"


# ---------------------------------------------------------------- integration with observability (GOV6, GOV12, GOV13)


async def test_eval_runs_treat_ask_rules_as_an_immediate_deny():
    """GOV12 (decision 1): eval runs never pause; an `ask` rule denies at once with the evaluation wording,
    no approval row or events, and the run is never awaiting_approval."""
    agent, v, run = _agent_and_run(policy=RM_ASK)
    reg = governance.ApprovalRegistry()
    res, evs, ex = await _run(v, agent, run, reg, source="eval")
    assert ex.cmds == [] and not any(e.type.startswith("approval") for e in evs)
    out = next(e.payload for e in evs if e.type == "tool_result")
    assert out["is_error"] and out["output"].startswith("Denied by policy during evaluation: rm -rf: bash matching")
    assert "approval_wait_ms" not in out
    assert governance.list_approvals(run["id"]) == [] and res.status == "succeeded"
    # the same policy outside evals still asks
    agent2, v2, run2 = _agent_and_run(policy={**RM_ASK, "timeout_seconds": 1})
    _, evs2, _ = await _run(v2, agent2, run2, governance.ApprovalRegistry(), source="api")
    assert any(e.type == "approval_requested" for e in evs2)


async def test_approval_wait_is_recorded_apart_from_tool_time():
    """GOV13: the wait for a reviewer is `approval_wait_ms` on the tool_result, not part of its `duration_ms`;
    the timeline shows it as an `approval` span and keeps it out of tool_ms; tool stats use duration_ms only."""
    agent, v, run = _agent_and_run(policy=RM_ASK)
    reg = governance.ApprovalRegistry()

    def on_req(e: Event) -> None:
        async def later() -> None:
            await asyncio.sleep(0.3)
            reg.resolve(e.payload["id"], "approve", "", "ui")
        asyncio.get_running_loop().create_task(later())

    _, evs, ex = await _run(v, agent, run, reg, on_request=on_req)
    assert ex.cmds == ["rm -rf build"]
    result = next(e.payload for e in evs if e.type == "tool_result")
    assert result["approval_wait_ms"] >= 250 and result["duration_ms"] < 100
    rows = [{"run_id": e.run_id, "seq": e.seq, "ts": e.ts, "type": e.type, "payload": e.payload} for e in evs]
    tl = metrics.build_timeline(db.get_run(run["id"]), rows)
    kinds = [sp["kind"] for sp in tl["spans"]]
    assert kinds.index("approval") == kinds.index("tool") - 1
    approval = next(sp for sp in tl["spans"] if sp["kind"] == "approval")
    tool = next(sp for sp in tl["spans"] if sp["kind"] == "tool")
    assert approval["duration_ms"] == result["approval_wait_ms"] and approval["label"] == "Approval for bash"
    assert approval["start_ms"] + approval["duration_ms"] <= tool["start_ms"] + 1
    assert tl["tool_ms"] == tool["duration_ms"] < 100 and tl["approval_ms"] >= 250
    assert tl["other_ms"] <= tl["total_ms"] - tl["approval_ms"] + 0.5  # rounding
    stats = metrics.tool_stats(metrics.tool_calls_from_events(rows))
    assert stats["tools"][0]["total_ms"] < 100


async def test_per_turn_cost_is_the_run_cost_everywhere(monkeypatch):
    """GOV6: the governor stores runs.cost_usd per turn with forge.pricing.cost; run_view, the timeline cost and
    Prometheus read that one stored value; a stopped tool-only turn still reports its usage."""
    from forge.server import observability
    from forge.server.views import run_view

    monkeypatch.setenv("FORGE_PRICES", '{"fake:loop": [1000, 2000]}')
    agent, v, run = _agent_and_run(model="fake:loop", cap=0.05)
    res, evs, _ = await _run(v, agent, run, governance.ApprovalRegistry())
    assert res.status == "budget_exceeded"
    err = next(e.payload for e in evs if e.type == "error")
    assert err["turn"] == 3 and err["tokens_in"] == 10 and err["tokens_out"] == 5
    db.update_run(run["id"], status=res.status, tokens_in=res.tokens_in, tokens_out=res.tokens_out)
    stored = db.get_run(run["id"])["cost_usd"]
    assert stored == pytest.approx(pricing.cost("fake:loop", res.tokens_in, res.tokens_out)) == pytest.approx(0.06)
    assert run_view(db.get_run(run["id"]))["cost_usd"] == stored
    db.update_run(run["id"], cost_usd=0.5)  # views never recompute: the stored value is the source of truth
    assert run_view(db.get_run(run["id"]))["cost_usd"] == 0.5
    text = observability.prometheus_families()
    cost = next(f for f in text if f["name"] == "forge_cost_usd_total")["samples"]
    assert any(lbl["agent"] == agent["slug"] and val == 0.5 for _, lbl, val in cost)
    runs_total = next(f for f in text if f["name"] == "forge_runs_total")["samples"]
    assert any(lbl["status"] == "budget_exceeded" for _, lbl, _v in runs_total)


def test_metrics_handle_governance_statuses():
    """GOV13: budget_exceeded is a finished failure with its own category; awaiting_approval is in progress."""
    t = time.time()
    rows = [{"id": f"r{i}", "status": s, "created_at": t, "started_at": t, "ended_at": t + 1 if s != "awaiting_approval"
             else None, "cost_usd": 0.0, "error": "Run stopped: per-run spending cap of $0.05 exceeded ($0.06 spent)."
             if s == "budget_exceeded" else None}
            for i, s in enumerate(("succeeded", "budget_exceeded", "awaiting_approval", "running"))]
    h = metrics.health(rows, [])
    assert (h["finished"], h["budget_exceeded"], h["in_progress"], h["awaiting_approval"]) == (2, 1, 2, 1)
    assert h["success_rate"] == 0.5
    found = metrics.classify_run(rows[1])
    assert found == [{"category": "budget_exceeded", "reason": rows[1]["error"], "count": 1}]
    assert metrics.failure_breakdown([(rows[1], found)])["categories"][0]["label"] == "Spending cap reached"
    b = metrics.series(rows, t - 10, t + 10, metrics.HOUR)
    assert sum(x["budget_exceeded"] for x in b) == 1 and sum(x["in_progress"] for x in b) == 2


def test_month_to_date_includes_judge_and_optimizer_calls(monkeypatch):
    """GOV7: grader calls (judge, optimizer) are agent spend outside any run and count toward the budget."""
    monkeypatch.setenv("FORGE_PRICES", '{"fake:grader": [1000, 1000]}')
    agent, _, run = _agent_and_run(monthly=1.0)
    db.update_run(run["id"], cost_usd=0.5)
    db.record_grader_call(agent["id"], "judge", "fake:grader", 300, 200)  # 0.5 USD
    assert budget.month_to_date_usd(agent["id"]) == pytest.approx(1.0)
    with pytest.raises(budget.BudgetExceeded):
        budget.ensure_budget(agent)


# ---------------------------------------------------------------- registry tools (tools x governance)


class _Scripted:
    """Provider that replays fixed responses."""

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)

    async def complete(self, system, messages, tools, temperature=None):  # noqa: ANN001
        from forge.types import ModelResponse

        return self.responses.pop(0) if self.responses else ModelResponse(text="done", tokens_in=1, tokens_out=1)


class _Recorder:
    """A registry tool source offering an MCP, an agent and an HTTP tool; records what actually ran."""

    kind = "mcp"

    def __init__(self) -> None:
        self.ran: list[str] = []

    async def open(self, notify) -> None:  # noqa: ANN001
        return None

    def specs(self):
        from forge.types import ToolSpec

        return [ToolSpec(name=n, description=n, parameters={"type": "object"})
                for n in ("mcp__docs__search", "agent__helper", "lookup")]

    async def call(self, name, args, deadline):  # noqa: ANN001
        from forge.tools.registry import ToolOutcome

        self.ran.append(name)
        return ToolOutcome(f"{name} ran")

    async def close(self) -> None:
        return None


REGISTRY_POLICY = {"rules": [
    {"tool": "mcp__docs__*", "pattern": "secret", "mode": "ask", "label": "docs secrets"},
    {"tool": "agent__*", "mode": "deny", "label": "no delegation"},
    {"tool": "lookup", "mode": "ask"},
], "timeout_seconds": 1}


def test_policy_rules_may_name_registry_tools():
    """Tools x governance: rules accept mcp__/agent__ names and prefixes, and HTTP tool names when the version
    has them; registry patterns search the arguments as JSON."""
    p = parse_policy(REGISTRY_POLICY, tools={"lookup"})
    assert match(p, "mcp__docs__search", {"q": "top secret"})[1].label == "docs secrets"
    assert match(p, "mcp__docs__search", {"q": "public"}) is None
    assert match(p, "mcp__other__x", {"q": "secret"}) is None
    assert match(p, "agent__anyone", {"input": "x"})[1].mode == "deny"
    with pytest.raises(ValueError, match="unknown tool 'lookup'"):
        parse_policy(REGISTRY_POLICY)  # not one of this version's HTTP tools
    with pytest.raises(ValueError, match="invalid regular expression"):
        parse_policy({"rules": [{"tool": "mcp__x__y", "pattern": "("}]})


async def test_approval_policies_gate_registry_tool_calls():
    """Tools x governance: before_tool sees mcp__, agent__ and HTTP tool calls from the registry. An ask rule
    pauses (approve runs the tool), a deny rule never runs it, an expired ask never runs it; in an eval run the
    ask is an immediate deny."""
    from forge.tools.registry import ToolRegistry
    from forge.types import ModelResponse, ToolCall

    def script() -> list:
        return [
            ModelResponse(tool_calls=[ToolCall(id="c1", name="mcp__docs__search", args={"q": "secret plan"})]),
            ModelResponse(tool_calls=[ToolCall(id="c2", name="agent__helper", args={"input": "x"})]),
            ModelResponse(tool_calls=[ToolCall(id="c3", name="lookup", args={"id": "1"})]),
            ModelResponse(text="finished"),
        ]

    agent, v, run = _agent_and_run(model="fake:echo", policy=REGISTRY_POLICY)
    v = {**v, "http_tools": [{"name": "lookup", "description": "", "parameters": {"type": "object"},
                              "url": "https://example.com"}]}
    reg = governance.ApprovalRegistry()
    approved: list[str] = []

    async def emit(e: Event) -> None:
        events.append(e)
        if e.type == "approval_requested" and e.payload["name"] == "mcp__docs__search":
            approved.append(e.payload["name"])
            reg.resolve(e.payload["id"], "approve", "", "ui")

    for source, expect_ran in (("api", ["mcp__docs__search"]), ("eval", [])):
        events: list[Event] = []
        rec = _Recorder()
        hooks = governance.RunGovernor(run["id"], agent, v, reg, source=source)
        av = AgentVersion(**{k: v[k] for k in AgentVersion.model_fields if k in v})
        res = await run_agent(av, "go", run_id=run["id"], provider=_Scripted(script()), executor=Exec(), emit=emit,
                              hooks=hooks, extra_tools=ToolRegistry([rec]))  # type: ignore[arg-type]
        assert res.status == "succeeded" and rec.ran == expect_ran, source
        out = {e.payload["name"]: e.payload for e in events if e.type == "tool_result"}
        assert out["agent__helper"]["output"].startswith("Denied by policy: no delegation")
        if source == "api":
            assert not out["mcp__docs__search"]["is_error"] and out["mcp__docs__search"]["source"] == "mcp"
            assert "approval_wait_ms" in out["mcp__docs__search"] and "approval_wait_ms" in out["lookup"]
            assert out["lookup"]["output"].startswith("Denied by reviewer: no decision within 1 seconds")
            asked = [e.payload["name"] for e in events if e.type == "approval_requested"]
            assert asked == ["mcp__docs__search", "lookup"]
        else:
            assert not any(e.type.startswith("approval") for e in events)
            for name in ("mcp__docs__search", "lookup"):
                assert out[name]["output"].startswith("Denied by policy during evaluation:"), name
    assert approved == ["mcp__docs__search"]


def test_child_runs_respect_the_callee_budget_and_cap(client, monkeypatch):
    """Tools x governance: agent__ child runs skip the concurrency semaphore but not the callee's monthly budget
    (refused, no child run) nor its per-run cap; the child's stored cost rolls into the parent's child_usage
    and is never part of the parent's own cost, so Prometheus counts it once."""
    from forge.server import observability

    monkeypatch.setenv("FORGE_PRICES", '{"fake:loop": [1000, 2000], "fake:tools": [1000, 2000]}')
    child = client.post("/v1/agents", json={"slug": "callee", "name": "c", "system_prompt": "s", "model": "fake:loop",
                                            "max_run_cost_usd": 0.03, "monthly_budget_usd": 0}).json()
    client.post("/v1/agents", json={"slug": "caller", "name": "p", "system_prompt": "s", "model": "fake:tools",
                                    "callable_agents": ["callee"]})
    hint = json.dumps({"tool": "agent__callee", "args": {"input": "go"}})
    r = client.post("/v1/agents/caller/runs", json={"input": hint, "wait": True}).json()
    res = next(e["payload"] for e in db.list_events(r["id"]) if e["type"] == "tool_result")
    assert res["is_error"] and "callee was not called: Monthly budget of $0.00 reached" in res["output"]
    assert db.list_runs(child["id"]) == []

    client.patch("/v1/agents/callee", json={"monthly_budget_usd": 10})
    r = client.post("/v1/agents/caller/runs", json={"input": hint, "wait": True}).json()
    [kid] = db.list_runs(child["id"])
    assert kid["source"] == "agent" and kid["parent_run_id"] == r["id"]
    assert kid["status"] == "budget_exceeded" and kid["cost_usd"] == pytest.approx(0.04)  # cap 0.03, 0.02 a turn
    parent = client.get(f"/v1/runs/{r['id']}").json()
    assert parent["child_usage"]["cost_usd"] == pytest.approx(kid["cost_usd"])
    assert parent["cost_usd"] == pytest.approx(pricing.cost("fake:tools", parent["tokens_in"], parent["tokens_out"]))
    total = next(f for f in observability.prometheus_families() if f["name"] == "forge_cost_usd_total")["samples"]
    by_agent = {lbl["agent"]: val for _, lbl, val in total}
    assert by_agent["callee"] == pytest.approx(0.04)
    assert by_agent["caller"] == pytest.approx(sum(x["cost_usd"] for x in db.list_runs(db.get_agent_by_slug("caller")["id"])))
