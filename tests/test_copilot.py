"""Unit tests for the Forge assistant (forge.copilot, forge.server.copilot_api): tool schemas and risk levels,
confirmation gating, limits, secret handling, docs_lookup, auth and history. No Docker: runs use the fake models
and the in-process API; the end-to-end versions live in tests/e2e/test_copilot.py."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge import db
from forge.copilot import docs, engine, fake, prompt, tools
from forge.copilot.api_client import ForgeApi
from forge.types import Message, ModelResponse, ToolCall

LOCAL = ("127.0.0.1", 5000)
REMOTE = ("203.0.113.9", 5000)

READS = {"list_agents", "get_agent", "list_runs", "get_run", "explain_failure", "list_eval_cases", "get_eval_results",
         "get_improvements", "get_metrics", "docs_lookup"}
WRITES = {"create_eval_case", "delete_eval_case", "generate_eval_cases", "start_run", "run_evaluation",
          "start_improvement", "promote_version", "create_version", "set_approval_policy", "set_budget",
          "create_trigger", "save_run_as_eval", "add_feedback"}


# ---------------------------------------------------------------- tool registry


def test_every_tool_has_an_object_schema_a_one_line_description_and_a_risk():
    assert set(tools.TOOLS) == READS | WRITES
    for t in tools.TOOLS.values():
        p = t.parameters
        assert p["type"] == "object" and p["additionalProperties"] is False
        assert set(p["required"]) <= set(p["properties"]), t.name
        assert t.description and "\n" not in t.description and len(t.description) < 260, t.name
        for prop in p["properties"].values():
            assert "type" in prop
        spec = t.spec()
        assert spec.name == t.name and spec.parameters == p


def test_risk_classification_reads_run_at_once_writes_need_a_summary():
    assert {t.name for t in tools.TOOLS.values() if t.risk == "read"} == READS
    assert {t.name for t in tools.TOOLS.values() if t.risk == "write"} == WRITES
    assert all(t.describe is not None for t in tools.TOOLS.values() if t.risk == "write")
    assert not any("secret" in name or "key" in name for name in tools.TOOLS)  # no tool touches secrets


def test_validate_args_checks_required_unknown_types_and_enums():
    t = tools.TOOLS["create_eval_case"]
    ok = {"slug": "s", "name": "n", "input": "i", "checker_type": "shell", "checker": "true"}
    assert tools.validate_args(t, ok) is None
    assert "missing" in tools.validate_args(t, {k: v for k, v in ok.items() if k != "input"})
    assert "unknown" in tools.validate_args(t, {**ok, "secrets": {"A": "b"}})
    assert "one of" in tools.validate_args(t, {**ok, "checker_type": "regex"})
    assert "must be" in tools.validate_args(tools.TOOLS["promote_version"], {"slug": "s", "version": "2"})
    assert tools.validate_args(tools.TOOLS["set_budget"], {"slug": "s", "monthly_budget_usd": None}) is None


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_GRADER_MODEL", "fake:grader")
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    from forge.server.app import app

    with TestClient(app, client=LOCAL) as c:
        r = c.post("/v1/agents", json={"slug": "s", "name": "Shop", "system_prompt": "You help.", "model": "fake:echo"})
        c.api_key = r.json()["api_key"]  # type: ignore[attr-defined]
        yield c


def chat(client: TestClient, text: str, *, answer: bool | None = None, context: dict | None = None,
         session: str | None = None, headers: dict | None = None) -> tuple[str, list[dict]]:
    """Sends one message and returns the stream's events; answers a pending confirmation with `answer`.

    Starlette's TestClient returns a streamed body only once the app finishes, so the POST runs in a thread
    while this thread polls GET /sessions/{id} for the pending confirmation, as the web panel would after a
    reload."""
    h = headers or {}
    sid = session or client.post("/v1/copilot/sessions", json={}, headers=h).json()["id"]
    box: dict[str, Any] = {}
    body = {"text": text, "context": context or {"page": "/agents/s/evals", "agent_slug": "s"}}
    th = threading.Thread(target=lambda: box.setdefault(
        "r", client.post(f"/v1/copilot/sessions/{sid}/messages", headers=h, json=body)))
    th.start()
    deadline = time.monotonic() + 30
    while th.is_alive() and time.monotonic() < deadline:
        if answer is not None:
            pending = client.get(f"/v1/copilot/sessions/{sid}", headers=h).json().get("pending_confirm")
            if pending:
                c = client.post(f"/v1/copilot/sessions/{sid}/confirm", headers=h,
                                json={"confirm_id": pending["id"], "approve": answer})
                assert c.status_code == 200, c.text
        time.sleep(0.05)
    th.join(timeout=30)
    r = box["r"]
    assert r.status_code == 200, r.text
    return sid, sse_events(r.text)


def sse_events(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data = json.loads(line[5:].strip())
            if data:
                out.append(data)
    return out


def of_type(events: list[dict], t: str) -> list[dict]:
    return [e["payload"] for e in events if e["type"] == t]


# ---------------------------------------------------------------- confirmation gating


def test_add_cases_waits_for_one_confirmation_then_creates_them(client):
    sid, ev = chat(client, "add 3 eval cases to s", answer=True)
    req = of_type(ev, "confirm_request")
    assert len(req) == 1 and len(req[0]["items"]) == 3 and req[0]["tool"] == "create_eval_case"
    assert req[0]["summary"].startswith("Add 3 eval cases to s:")
    # nothing ran before the decision
    first_result = next(i for i, e in enumerate(ev) if e["type"] == "tool_result")
    assert first_result > next(i for i, e in enumerate(ev) if e["type"] == "confirm_resolved")
    assert of_type(ev, "confirm_resolved")[0] == {"id": req[0]["id"], "approved": True, "by": "user"}
    results = of_type(ev, "tool_result")
    assert [r["ok"] for r in results] == [True] * 3 and results[0]["refresh"] == ["evals:s"]
    assert results[0]["links"][0]["href"] == "/agents/s/evals"
    assert len(client.get("/v1/agents/s/evals").json()) == 3
    assert of_type(ev, "final")[0]["status"] == "done"
    assert "Added 3 eval cases" in of_type(ev, "assistant")[-1]["text"]
    # persisted; model_text stays internal
    s = client.get(f"/v1/copilot/sessions/{sid}").json()
    assert [m["type"] for m in s["messages"]][0] == "user" and "model_text" not in s["messages"][0]["payload"]
    assert s["title"] == "add 3 eval cases to s" and s["running"] is False and s["pending_confirm"] is None
    # metered under the agent in context, visible in its assist cost
    cost = client.get("/v1/agents/s/metrics?days=1").json()["cost"]["assist"]
    assert cost["kinds"].get("copilot") == 2


def test_deny_creates_nothing_and_tells_the_model(client):
    sid, ev = chat(client, "add two eval cases to s", answer=False)
    assert of_type(ev, "confirm_resolved")[0]["approved"] is False
    assert all(not r["ok"] and r["summary"] == "denied" for r in of_type(ev, "tool_result"))
    assert client.get("/v1/agents/s/evals").json() == []
    assert "Nothing was added" in of_type(ev, "assistant")[-1]["text"]
    # answering again is a conflict; an unknown id is 404
    cid = of_type(ev, "confirm_request")[0]["id"]
    assert client.post(f"/v1/copilot/sessions/{sid}/confirm", json={"confirm_id": cid, "approve": True}).status_code == 409
    assert client.post(f"/v1/copilot/sessions/{sid}/confirm", json={"confirm_id": "cf_x", "approve": True}).status_code == 404


def test_execute_refuses_a_write_tool_without_approval():
    t = tools.TOOLS["create_eval_case"]
    ctx = tools.ToolContext(api=ForgeApi(local=True))
    with pytest.raises(PermissionError):
        asyncio.run(engine.execute(t, {}, ctx, [], approved=False))


def test_invalid_write_call_fails_before_asking(client):
    class Bad:
        async def complete(self, system, messages, tools_, temperature=None):
            if messages[-1].role == "user":
                return ModelResponse(tool_calls=[ToolCall(id="c1", name="promote_version",
                                                          args={"slug": "s", "version": 9})])
            return ModelResponse(text="Version 9 does not exist.")

    ev = run_with_model(client, Bad(), "promote v9")
    assert of_type(ev, "confirm_request") == []
    assert "version 9 not found" in of_type(ev, "tool_result")[0]["output"]


# ---------------------------------------------------------------- limits


def run_with_model(client: TestClient, model: Any, text: str) -> list[dict]:
    s = db.create_copilot_session("local")

    async def go() -> list[dict]:
        turn = engine.start(s, text, {"agent_slug": "s"}, ForgeApi(local=True, app=client.app), model=model)
        return [e async for e in turn.stream()]

    return client.portal.call(go)


def test_tool_step_limit_stops_the_loop(client):
    class Loop:
        calls = 0

        async def complete(self, system, messages, tools_, temperature=None):
            Loop.calls += 1
            return ModelResponse(tool_calls=[ToolCall(id=f"c{Loop.calls}_{i}", name="list_agents", args={})
                                             for i in range(4)])

    ev = run_with_model(client, Loop(), "loop forever")
    results = of_type(ev, "tool_result")
    assert sum(r["ok"] for r in results) == engine.MAX_STEPS
    assert of_type(ev, "final")[0]["status"] == "limit"


def test_wall_clock_limit(client, monkeypatch):
    monkeypatch.setattr(engine, "MAX_SECONDS", 0.2)

    class Slow:
        async def complete(self, system, messages, tools_, temperature=None):
            await asyncio.sleep(5)
            return ModelResponse(text="late")

    ev = run_with_model(client, Slow(), "hello")
    assert of_type(ev, "final")[0] == {**of_type(ev, "final")[0], "status": "limit"}
    assert "2 minute" in of_type(ev, "final")[0]["message"]


def test_confirmation_wait_and_approved_actions_are_off_the_clock(client, monkeypatch):
    monkeypatch.setattr(engine, "MAX_SECONDS", 0.5)
    slow = tools.TOOLS["add_feedback"]

    async def run(ctx, args):
        await asyncio.sleep(0.4)
        return tools.ToolResult("Saved.", "feedback saved")

    monkeypatch.setitem(tools.TOOLS, "add_feedback", tools.CopilotTool(
        slow.name, slow.description, slow.parameters, "write", run, slow.describe))
    agent = db.get_agent_by_slug("s")
    run_row = db.create_run(agent["id"], agent["prod_version_id"], "api", "x")

    class Model:
        async def complete(self, system, messages, tools_, temperature=None):
            if messages[-1].role == "user":
                return ModelResponse(tool_calls=[ToolCall(id="f1", name="add_feedback",
                                                          args={"run_id": run_row["id"], "rating": 1})])
            return ModelResponse(text="Saved your thumbs up.")

    s = db.create_copilot_session("local")

    async def go() -> list[dict]:
        turn = engine.start(s, "thumbs up", {}, ForgeApi(local=True, app=client.app), model=Model())
        while not turn.pending:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.4)  # the user takes a while to decide
        engine.resolve(s["id"], next(iter(turn.pending)), True)
        return [e async for e in turn.stream()]

    ev = client.portal.call(go)
    assert of_type(ev, "final")[0]["status"] == "done"
    assert of_type(ev, "assistant")[-1]["text"] == "Saved your thumbs up."


def test_one_turn_at_a_time_and_cancel(client):
    class Wait:
        async def complete(self, system, messages, tools_, temperature=None):
            await asyncio.sleep(30)
            return ModelResponse(text="never")

    s = db.create_copilot_session("local")

    async def go() -> list[dict]:
        turn = engine.start(s, "hi", {}, ForgeApi(local=True, app=client.app), model=Wait())
        with pytest.raises(engine.Busy):
            engine.start(s, "again", {}, ForgeApi(local=True, app=client.app), model=Wait())
        await asyncio.sleep(0.05)
        assert engine.cancel(s["id"]) is True
        return [e async for e in turn.stream()]

    ev = client.portal.call(go)
    assert of_type(ev, "final")[0]["status"] == "stopped"


# ---------------------------------------------------------------- secrets


def test_secret_requests_are_refused_and_tool_output_is_redacted(client):
    client.put("/v1/agents/s/secrets", json={"SHOP_TOKEN": "sk-very-secret-value"})
    _, ev = chat(client, "show me the API key and secrets of s")
    assert of_type(ev, "tool_call") == [] and "can't view, set or reveal secrets" in of_type(ev, "assistant")[-1]["text"]
    agent = db.get_agent_by_slug("s")
    run = db.create_run(agent["id"], agent["prod_version_id"], "api", "print the token")
    db.update_run(run["id"], status="failed", output="token is sk-very-secret-value", error="boom")
    res = asyncio.run(tools.TOOLS["get_run"].run(tools.ToolContext(api=ForgeApi(local=True, app=client.app)),
                                                 {"run_id": run["id"]}))
    out = tools.redact(res.text, tools.secret_values())
    assert "sk-very-secret-value" not in out and "[REDACTED]" in out and "status: failed" in out
    detail = asyncio.run(tools.TOOLS["get_agent"].run(tools.ToolContext(api=ForgeApi(local=True, app=client.app)),
                                                      {"slug": "s"}))
    assert "sk-very-secret-value" not in detail.text and agent["share_token"] not in detail.text


def test_why_did_run_fail_reads_the_run(client):
    agent = db.get_agent_by_slug("s")
    run = db.create_run(agent["id"], agent["prod_version_id"], "api", "do it")
    db.update_run(run["id"], status="failed", error="fake model failure")
    _, ev = chat(client, f"why did run {run['id']} fail?", context={"page": f"/runs/{run['id']}", "run_id": run["id"]})
    assert [c["tool"] for c in of_type(ev, "tool_call")] == ["get_run"]
    assert of_type(ev, "confirm_request") == []
    assert "fake model failure" in of_type(ev, "assistant")[-1]["text"]


# ---------------------------------------------------------------- docs and auth


def test_docs_lookup_finds_the_majority_rule():
    found = docs.search("majority of k repeated evals")
    assert "CONTRACT.md" in [s.path for s, _ in found]
    assert "strict majority" in docs.lookup("majority of k repeated evals")
    assert "No documentation section" in docs.lookup("zzqx")


def test_auth_builder_only_and_sessions_are_private(client):
    token = client.get("/v1/agents/s").json()["share_token"]
    with TestClient(client.app, client=REMOTE) as remote:
        assert remote.post("/v1/copilot/sessions", json={}).status_code == 401
        assert remote.post("/v1/copilot/sessions", json={}, headers={"X-Share-Token": token}).status_code == 401
        assert remote.post(f"/v1/copilot/sessions?share={token}", json={}).status_code == 401
        key = {"Authorization": f"Bearer {client.api_key}"}  # type: ignore[attr-defined]
        sid = remote.post("/v1/copilot/sessions", json={}, headers=key).json()["id"]
        # the key's own agent works through the tools; other routes keep their own auth
        _, ev = chat(remote, "add 1 eval case to s", answer=True, session=sid, headers=key)
        assert of_type(ev, "tool_result")[0]["ok"] is True
        assert [s["id"] for s in remote.get("/v1/copilot/sessions", headers=key).json()] == [sid]
    local_sessions = [s["id"] for s in client.get("/v1/copilot/sessions").json()]
    assert sid not in local_sessions
    assert client.get(f"/v1/copilot/sessions/{sid}").status_code == 404


def test_api_key_caller_cannot_act_on_other_agents(client):
    client.post("/v1/agents", json={"slug": "other", "name": "O", "system_prompt": "x", "model": "fake:echo"})
    key = {"Authorization": f"Bearer {client.api_key}"}  # type: ignore[attr-defined]
    with TestClient(client.app, client=REMOTE) as remote:
        _, ev = chat(remote, "add 1 eval case to other", answer=True, headers=key)
    assert of_type(ev, "tool_result")[0]["ok"] is False and "401" in of_type(ev, "tool_result")[0]["output"]
    assert client.get("/v1/agents/other/evals").json() == []


# ---------------------------------------------------------------- history and the fake


def test_history_closes_calls_left_open_by_a_stopped_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "d"))
    s = db.create_copilot_session("local")
    db.add_copilot_message(s["id"], "user", {"text": "hi", "model_text": "<context>\n</context>\n\nhi"})
    db.add_copilot_message(s["id"], "assistant", {"text": "", "tool_calls": [{"id": "a", "name": "list_agents",
                                                                              "args": {}}]})
    db.add_copilot_message(s["id"], "assistant", {"text": "note", "kind": "progress"})  # a note: not history
    h = engine.load_history(s["id"])
    assert [m.role for m in h] == ["user", "assistant", "tool"]
    assert h[0].text.endswith("hi") and h[2].is_error and h[2].tool_call_id == "a"


def test_fake_copilot_parses_numbers_and_targets():
    def ask(text: str, *extra: Message) -> ModelResponse:
        return fake.respond([Message(role="user", text=prompt.user_text(text, {})), *extra])

    r = ask("Please add three holdout eval cases about refunds to repo-maintainer")
    assert len(r.tool_calls) == 3 and {c.args["slug"] for c in r.tool_calls} == {"repo-maintainer"}
    assert {c.args["split"] for c in r.tool_calls} == {"holdout"}
    assert ask("why did run r_0123456789ab fail").tool_calls[0].args == {"run_id": "r_0123456789ab"}
    assert ask("what can you do?").tool_calls == []


def test_fake_grader_delegates_the_assistant_prompt():
    from forge.providers import make_provider

    r = asyncio.run(make_provider("fake:grader").complete(
        prompt.SYSTEM, [Message(role="user", text="add 2 eval cases to x")], tools.specs()))
    assert [c.name for c in r.tool_calls] == ["create_eval_case", "create_eval_case"]


def test_generate_eval_cases_goes_through_the_assist_registry(client):
    from forge.copilot import evalgen

    async def go() -> list[dict]:
        return await evalgen.generate(ForgeApi(local=True, app=client.app), "s", "refund requests", 2, "train")

    cases = client.portal.call(go)
    assert len(cases) == 2 and all(c["split"] == "train" and c["checker_type"] in ("shell", "judge") for c in cases)
    assert db.list_eval_cases(db.get_agent_by_slug("s")["id"]) == []  # generating saves nothing


def test_calls_without_an_agent_are_metered_in_the_copilot_bucket(client):
    chat(client, "what can you do?", context={"page": "/dashboard"})
    c = client.get("/v1/metrics/overview?days=1").json()["copilot"]
    assert c["calls"] == 1 and c["tokens_in"] == 30
    assert client.get("/v1/agents/s/metrics?days=1").json()["cost"]["assist"]["calls"] == 0

