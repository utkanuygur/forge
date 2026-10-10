"""Governance unit tests: approval policy matching, the approval pause, spending caps and pricing (GOV1..GOV9)."""

from __future__ import annotations

import asyncio
import sys
import time
import types
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge import budget, db, governance
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


def test_fallback_prices_and_free_providers():
    """GOV6: the fallback table prices Claude models per million tokens; fake and openai_compat are free;
    unknown models are None; dated model ids match by prefix."""
    assert budget.estimate_cost_usd("anthropic:claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)
    assert budget.estimate_cost_usd("anthropic:claude-opus-5-5", 0, 1_000_000) == pytest.approx(20.0)
    assert budget.estimate_cost_usd("anthropic:claude-sonnet-5-5", 500_000, 100_000) == pytest.approx(2.0)
    assert budget.estimate_cost_usd("anthropic:claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.6)
    assert budget.estimate_cost_usd("anthropic:claude-sonnet-5-5-20261001", 1_000_000, 0) == pytest.approx(2.0)
    assert budget.estimate_cost_usd("fake:hello", 10**9, 10**9) == 0.0
    assert budget.estimate_cost_usd("openai_compat:http://x/v1|qwen", 10**9, 10**9) == 0.0
    assert budget.estimate_cost_usd("anthropic:some-unknown-model", 1, 1) is None


def test_forge_prices_override_and_pricing_module(monkeypatch):
    """GOV6: FORGE_PRICES overrides any price (by full model string or id); forge.pricing is used when present."""
    monkeypatch.setenv("FORGE_PRICES", '{"fake:loop": [1000, 2000], "claude-opus-5-5": {"in": 1, "out": 1}}')
    assert budget.estimate_cost_usd("fake:loop", 10, 5) == pytest.approx(0.02)
    assert budget.estimate_cost_usd("anthropic:claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(2.0)
    monkeypatch.setenv("FORGE_PRICES", "not json")
    assert budget.estimate_cost_usd("fake:loop", 10, 5) == 0.0
    monkeypatch.delenv("FORGE_PRICES")
    fake = types.ModuleType("forge.pricing")
    fake.estimate_cost_usd = lambda model, i, o: 42.0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "forge.pricing", fake)
    assert budget.estimate_cost_usd("anthropic:claude-opus-5-5", 1, 1) == 42.0


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
               on_request=None) -> tuple[Any, list[Event], Exec]:
    events: list[Event] = []
    ex = Exec()

    async def emit(e: Event) -> None:
        events.append(e)
        if e.type == "approval_requested" and on_request:
            on_request(e)

    hooks = governance.RunGovernor(run["id"], agent, version, registry)
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
    run = db.create_run(a["id"], agent["prod_version_id"], "api", "x", status="awaiting_approval")
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

    assert db.fail_interrupted_runs() >= 1
    r2 = db.get_run(run["id"])
    assert (r2["status"], r2["error"]) == ("failed", "interrupted")
    assert governance.get_approval(ap["id"])["status"] == "expired"
