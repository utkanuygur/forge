"""Share and API conversations: visitor scoping, every share link limit, budgets on share turns, expiry, Slack
threads (CONTRACT.md "Share and API conversations")."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from forge import db, share_limits

REMOTE = ("203.0.113.9", 5000)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_EXECUTOR", "local")
    monkeypatch.setenv("FORGE_SCHEDULER", "0")
    for k in ("FORGE_TRUST_ALL", "FORGE_PRICES", *share_limits.ENV.values(), "FORGE_SESSION_IDLE_HOURS"):
        monkeypatch.delenv(k, raising=False)
    from forge.server.app import app

    with TestClient(app, client=("127.0.0.1", 5000)) as builder, TestClient(app, client=REMOTE) as far:
        yield builder, far


def make_agent(builder, slug: str, model: str = "fake:echo", **kw) -> dict:
    r = builder.post("/v1/agents", json={"slug": slug, "name": slug, "system_prompt": "s", "model": model, **kw})
    assert r.status_code == 200, r.text
    return r.json()


class Visitor:
    """A share page visitor: the share token plus the visitor id the server issued on its first session."""

    def __init__(self, far: TestClient, agent: dict) -> None:
        self.far, self.slug, self.token, self.vid = far, agent["slug"], agent["share_token"], None

    def headers(self) -> dict:
        h = {"X-Share-Token": self.token}
        if self.vid:
            h["X-Forge-Visitor"] = self.vid
        return h

    def new_session(self):
        r = self.far.post(f"/v1/agents/{self.slug}/sessions", json={}, headers=self.headers())
        if r.status_code == 200:
            self.vid = r.json()["visitor_id"]
            assert r.headers["X-Forge-Visitor"] == self.vid
        return r

    def send(self, sid: str, text: str = "hi", wait: bool = True):
        return self.far.post(f"/v1/sessions/{sid}/messages", json={"input": text, "wait": wait},
                             headers=self.headers())

    def get(self, path: str, **kw):
        return self.far.get(f"/v1{path}", headers=self.headers(), **kw)


def poll(fn, timeout: float = 15.0):
    end = time.time() + timeout
    while time.time() < end:
        if (v := fn()) is not None:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


# ---------------------------------------------------------------- scoping


def test_share_conversation_runs_prod_and_carries_the_workspace(env):
    builder, far = env
    agent = make_agent(builder, "sh-conv", model="fake:session")
    builder.post("/v1/agents/sh-conv/versions", json={"system_prompt": "a draft nobody promoted"})
    a = Visitor(far, agent)
    s = a.new_session().json()
    assert s["channel"] == "share" and s["version_n"] == 1 and s["expired"] is False and s["max_turns"] == 20
    assert "draft" not in s and "base_version_n" not in s  # no configuration for visitors
    t1 = a.send(s["id"], "create hello.py").json()
    assert t1["source"] == "share" and t1["status"] == "succeeded" and t1["version_n"] == 1
    t2 = a.send(s["id"], "what is in hello.py?").json()
    assert t2["output"] == "Turn 2: hello.py contains `print('hello from forge')` (history had 1 earlier turn(s))."
    files = a.get(f"/runs/{t2['id']}/files").json()
    assert "hello.py" in [f["path"] for f in files]  # (macOS LocalExecutor archives may add ._ files)
    assert a.get(f"/runs/{t2['id']}/artifacts").status_code == 200
    # feedback per turn works as on the share page
    fb = far.post(f"/v1/runs/{t1['id']}/feedback", json={"rating": 1}, headers=a.headers())
    assert fb.status_code == 200 and fb.json()["source"] == "share"
    # share callers cannot choose a version or a draft
    assert far.post("/v1/agents/sh-conv/sessions", json={"version": 2}, headers=a.headers()).status_code == 400
    assert far.post("/v1/agents/sh-conv/sessions", json={"draft": {"system_prompt": "x"}},
                    headers=a.headers()).status_code == 400


def test_visitors_never_see_each_other_or_the_builder(env):
    builder, far = env
    agent = make_agent(builder, "sh-scope")
    a, b = Visitor(far, agent), Visitor(far, agent)
    sa = a.new_session().json()
    ta = a.send(sa["id"], "secret plan").json()
    sb = b.new_session().json()
    assert a.vid != b.vid and a.vid.startswith("vis_")
    # B cannot read, message, list or browse A's session or runs: 404, not 401
    assert b.get(f"/sessions/{sa['id']}").status_code == 404
    assert b.send(sa["id"]).status_code == 404
    for path in ("", "/events.json", "/files", "/artifacts", "/diff", "/approvals"):
        assert b.get(f"/runs/{ta['id']}{path}").status_code == 404, path
    assert far.post(f"/v1/runs/{ta['id']}/feedback", json={"rating": -1}, headers=b.headers()).status_code == 404
    assert far.post(f"/v1/runs/{ta['id']}/cancel", headers=b.headers()).status_code == 404
    assert [x["id"] for x in b.get("/agents/sh-scope/sessions").json()] == [sb["id"]]
    assert b.get("/agents/sh-scope/runs").json() == []
    assert [x["id"] for x in a.get("/agents/sh-scope/runs").json()] == [ta["id"]]
    # SSE and download links carry the visitor as a query parameter
    q = {"share": agent["share_token"], "visitor": a.vid}
    assert far.get(f"/v1/runs/{ta['id']}/events.json", params=q).status_code == 200
    assert far.get(f"/v1/runs/{ta['id']}/events.json", params={**q, "visitor": b.vid}).status_code == 404
    # A token alone (no visitor) sees no visitor's sessions or runs
    tok = {"X-Share-Token": agent["share_token"]}
    assert far.get(f"/v1/sessions/{sa['id']}", headers=tok).status_code == 404
    assert far.get(f"/v1/runs/{ta['id']}", headers=tok).status_code == 404
    assert far.get("/v1/agents/sh-scope/sessions", headers=tok).json() == []
    # the builder's Playground sessions and runs stay invisible
    ps = builder.post("/v1/agents/sh-scope/sessions", json={}).json()
    pt = builder.post(f"/v1/sessions/{ps['id']}/messages", json={"input": "builder", "wait": True}).json()
    assert a.get(f"/sessions/{ps['id']}").status_code == 404
    assert a.get(f"/runs/{pt['id']}").status_code == 404
    # the builder lists Playground sessions by default and share sessions on request
    assert [x["id"] for x in builder.get("/v1/agents/sh-scope/sessions").json()] == [ps["id"]]
    shared = builder.get("/v1/agents/sh-scope/sessions", params={"channel": "share"}).json()
    assert {x["id"] for x in shared} == {sa["id"], sb["id"]}
    # a forged visitor id is replaced by a fresh one
    r = far.post("/v1/agents/sh-scope/sessions", json={}, headers={**tok, "X-Forge-Visitor": "vis_made_up"})
    assert r.json()["visitor_id"] not in ("vis_made_up", a.vid, b.vid)
    # another agent's share token gets nothing
    other = make_agent(builder, "sh-other")
    assert far.get(f"/v1/sessions/{sa['id']}", headers={"X-Share-Token": other["share_token"],
                                                         "X-Forge-Visitor": a.vid}).status_code == 401


def test_visitor_can_stop_its_own_turn(env):
    builder, far = env
    agent = make_agent(builder, "sh-stop", model="fake:slow")
    a = Visitor(far, agent)
    s = a.new_session().json()
    t = a.send(s["id"], "wait", wait=False).json()
    assert a.send(s["id"], "again").status_code == 409
    r = far.post(f"/v1/runs/{t['id']}/cancel", headers=a.headers())
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


# ---------------------------------------------------------------- limits


def test_session_turn_limit(env):
    builder, far = env
    agent = make_agent(builder, "sh-turns")
    assert builder.patch("/v1/agents/sh-turns", json={"share_limits": {"session_turns": 2}}).status_code == 200
    a = Visitor(far, agent)
    s = a.new_session().json()
    assert s["max_turns"] == 2
    assert a.send(s["id"]).status_code == 200 and a.send(s["id"]).status_code == 200
    r = a.send(s["id"])
    assert r.status_code == 429 and r.json()["limit"] == "session_turns" and "Retry-After" in r.headers
    assert "Start a new conversation" in r.json()["detail"]


def test_sessions_per_visitor_limit_and_expiry(env, monkeypatch):
    builder, far = env
    agent = make_agent(builder, "sh-open")
    builder.patch("/v1/agents/sh-open", json={"share_limits": {"sessions_per_visitor": 2}})
    a = Visitor(far, agent)
    s1 = a.new_session().json()
    a.new_session()
    r = a.new_session()
    assert r.status_code == 429 and r.json()["limit"] == "sessions_per_visitor"
    assert 0 < int(r.headers["Retry-After"]) <= 24 * 3600
    # another visitor is not affected
    assert Visitor(far, agent).new_session().status_code == 200
    # after 24 idle hours the conversations expire: read only, and they free the visitor's slots
    later = time.time() + 24 * 3600 + 5
    monkeypatch.setattr(share_limits, "now", lambda: later)
    got = a.get(f"/sessions/{s1['id']}").json()
    assert got["expired"] is True and got["id"] == s1["id"]
    sent = a.send(s1["id"])
    assert sent.status_code == 410 and "expired" in sent.json()["detail"]
    assert a.new_session().status_code == 200


def test_daily_limit_rolls_over_at_utc_midnight(env, monkeypatch):
    builder, far = env
    agent = make_agent(builder, "sh-daily")
    builder.patch("/v1/agents/sh-daily", json={"share_limits": {"daily_turns": 2, "session_turns": 50}})
    t0 = time.time()
    monkeypatch.setattr(share_limits, "now", lambda: t0)
    a = Visitor(far, agent)
    s = a.new_session().json()
    assert a.send(s["id"]).status_code == 200
    # one shot share runs count toward the same daily budget
    one = far.post("/v1/agents/sh-daily/runs", json={"input": "x", "wait": True}, headers=a.headers())
    assert one.status_code == 200 and one.json()["source"] == "share"
    r = a.send(s["id"])
    assert r.status_code == 429 and r.json()["limit"] == "daily_turns"
    assert r.json()["detail"] == "You've reached today's limit for this link. Try again tomorrow."
    midnight = share_limits.day_start(t0) + 86400
    assert int(r.headers["Retry-After"]) == int(midnight - t0 + 0.999)
    assert far.post("/v1/agents/sh-daily/runs", json={"input": "x"}, headers=a.headers()).status_code == 429
    monkeypatch.setattr(share_limits, "now", lambda: midnight + 1)  # the next UTC day
    assert a.send(s["id"]).status_code == 200


def test_concurrent_turn_limit(env):
    builder, far = env
    agent = make_agent(builder, "sh-busy", model="fake:slow")
    builder.patch("/v1/agents/sh-busy", json={"share_limits": {"concurrent_turns": 1}})
    a, b = Visitor(far, agent), Visitor(far, agent)
    sa, sb = a.new_session().json(), b.new_session().json()
    ta = a.send(sa["id"], wait=False).json()
    r = b.send(sb["id"], wait=False)
    assert r.status_code == 429 and r.json()["limit"] == "concurrent_turns" and r.headers["Retry-After"] == "15"
    # builder Playground turns never count against the share link
    ps = builder.post("/v1/agents/sh-busy/sessions", json={}).json()
    pt = builder.post(f"/v1/sessions/{ps['id']}/messages", json={"input": "x"})
    assert pt.status_code == 200
    builder.post(f"/v1/runs/{pt.json()['id']}/cancel")
    far.post(f"/v1/runs/{ta['id']}/cancel", headers=a.headers())
    assert b.send(sb["id"], wait=False).status_code == 200
    run = b.get(f"/sessions/{sb['id']}").json()["turns"][0]
    far.post(f"/v1/runs/{run['id']}/cancel", headers=b.headers())


def test_global_defaults_from_env_and_agent_overrides(env, monkeypatch):
    builder, _ = env
    monkeypatch.setenv("FORGE_SHARE_DAILY_TURNS", "7")
    agent = make_agent(builder, "sh-defaults")
    assert agent["share_limits_effective"] == {"session_turns": 20, "sessions_per_visitor": 3, "daily_turns": 7,
                                               "concurrent_turns": 2}
    assert agent["share_limits"] == {k: None for k in share_limits.LIMIT_KEYS}
    p = builder.patch("/v1/agents/sh-defaults", json={"share_limits": {"concurrent_turns": 5}}).json()
    assert p["share_limits"]["concurrent_turns"] == 5 and p["share_limits_effective"]["daily_turns"] == 7
    p = builder.patch("/v1/agents/sh-defaults", json={"share_limits": {"session_turns": 4}}).json()
    assert p["share_limits"]["concurrent_turns"] == 5 and p["share_limits"]["session_turns"] == 4  # merged
    p = builder.patch("/v1/agents/sh-defaults", json={"share_limits": {"concurrent_turns": None}}).json()
    assert p["share_limits"]["concurrent_turns"] is None and p["share_limits_effective"]["concurrent_turns"] == 2
    assert builder.patch("/v1/agents/sh-defaults", json={"share_limits": {"daily_turns": 0}}).status_code == 422


# ---------------------------------------------------------------- governance


def test_monthly_budget_applies_to_share_turns(env):
    builder, far = env
    agent = make_agent(builder, "sh-budget", monthly_budget_usd=0)
    a = Visitor(far, agent)
    s = a.new_session().json()
    r = a.send(s["id"])
    assert r.status_code == 402
    assert r.json()["detail"] == "This agent has reached its usage limit for this month. Try again later."
    assert db.session_runs(s["id"]) == []
    one = far.post("/v1/agents/sh-budget/runs", json={"input": "x"}, headers=a.headers())
    assert one.status_code == 402 and "$" not in one.json()["detail"]


def test_per_run_cap_applies_to_share_turns(env, monkeypatch):
    builder, far = env
    monkeypatch.setenv("FORGE_PRICES", '{"fake:loop": [1000, 2000]}')  # 0.02 USD per fake turn
    agent = make_agent(builder, "sh-cap", model="fake:loop", max_run_cost_usd=0.05)
    a = Visitor(far, agent)
    s = a.new_session().json()
    t = a.send(s["id"]).json()
    assert t["status"] == "budget_exceeded" and t["source"] == "share"


# ---------------------------------------------------------------- API callers


def test_api_key_conversation_with_a_version(env):
    builder, far = env
    agent = make_agent(builder, "sh-api", model="fake:session")
    builder.post("/v1/agents/sh-api/versions", json={"system_prompt": "v2"})
    key = {"Authorization": f"Bearer {agent['api_key']}"}
    s = far.post("/v1/agents/sh-api/sessions", json={"version": 2}, headers=key).json()
    assert s["channel"] == "api" and s["version_n"] == 2
    t1 = far.post(f"/v1/sessions/{s['id']}/messages", json={"input": "create", "wait": True}, headers=key).json()
    t2 = far.post(f"/v1/sessions/{s['id']}/messages", json={"input": "read", "wait": True}, headers=key).json()
    assert t1["source"] == "api" and t2["version_n"] == 2 and "hello from forge" in t2["output"]
    assert [x["id"] for x in far.get("/v1/agents/sh-api/sessions", headers=key).json()] == [s["id"]]
    # API sessions have no share limits and do not expire
    assert "expired" not in far.get(f"/v1/sessions/{s['id']}", headers=key).json()


# ---------------------------------------------------------------- Slack threads


@pytest.mark.asyncio
async def test_slack_thread_sessions(env, monkeypatch):
    from forge.triggers import core, store

    builder, _ = env
    agent = make_agent(builder, "sh-slack", model="fake:session")
    builder.patch("/v1/agents/sh-slack", json={"share_limits": {"session_turns": 2}})
    t = store.create(agent["id"], "slack", core.validate_config("slack", {}), secret="x", tokens={}, enabled=True)
    key = core.thread_key(t["id"], "C1", "111.1")
    from forge.server import runs

    r1 = await core.fire(t, "create hello.py", session_key=key)
    await runs.get_manager().wait(r1.run_id)
    r2 = await core.fire(t, "what is in it?", session_key=key)
    run2 = await runs.get_manager().wait(r2.run_id)
    assert run2["source"] == "slack" and run2["turn_index"] == 1 and "hello from forge" in run2["output"]
    other = await core.fire(t, "create hello.py", session_key=core.thread_key(t["id"], "C1", "222.2"))
    await runs.get_manager().wait(other.run_id)
    assert db.get_run(other.run_id)["session_id"] != run2["session_id"]
    full = await core.fire(t, "third", session_key=key)
    assert full.status == "skipped" and "limit of 2 messages" in full.detail
    # an expired thread conversation starts over
    later = time.time() + 25 * 3600
    monkeypatch.setattr(share_limits, "now", lambda: later)
    fresh = await core.fire(t, "create hello.py", session_key=key)
    run = await runs.get_manager().wait(fresh.run_id)
    assert run["turn_index"] == 0 and run["session_id"] != run2["session_id"]
