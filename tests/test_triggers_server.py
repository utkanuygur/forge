"""Layer 1: trigger scheduler with a fake clock and the trigger HTTP API with a fake run manager."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import pytest
from fastapi.testclient import TestClient

from forge import db
from forge.server import runs
from forge.triggers import core, store
from forge.triggers.scheduler import Scheduler
from forge.triggers.security import github_signature, slack_signature

LOCAL = ("127.0.0.1", 5000)
REMOTE = ("203.0.113.9", 5000)


class FakeManager:
    """Creates real run rows that stay `running` until finish() is called."""

    def __init__(self) -> None:
        self.started: list[dict] = []

    async def start_run(self, agent, version, input, source, **kw):
        r = db.create_run(agent["id"], version["id"], source, input, status="running")
        self.started.append({"id": r["id"], "input": input, "source": source, "version_n": version["n"]})
        return r["id"]

    def finish(self, run_id: str, output: str = "all done", status: str = "succeeded") -> None:
        db.update_run(run_id, status=status, output=output, ended_at=time.time())

    async def wait(self, run_id: str):
        for _ in range(500):
            r = db.get_run(run_id)
            if r["status"] not in ("queued", "running"):
                return r
            await asyncio.sleep(0.01)
        return db.get_run(run_id)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_SCHEDULER", "0")
    monkeypatch.setenv("FORGE_TRIGGER_RETRY_BACKOFF", "0")
    for k in ("FORGE_TRUST_ALL", "FORGE_PUBLIC_URL", "FORGE_TEST_CLOCK_FILE", "FORGE_SLACK_RESPONSE_URL_PREFIXES",
              "FORGE_TRIGGER_API_HOSTS", "FORGE_PRICES"):
        monkeypatch.delenv(k, raising=False)
    core.limiter.reset()


@pytest.fixture
def mgr(monkeypatch) -> FakeManager:
    m = FakeManager()
    monkeypatch.setattr(runs, "get_manager", lambda: m)
    return m


def T(*a: int) -> float:
    return datetime(*a, tzinfo=timezone.utc).timestamp()


def make_agent_row(model: str = "fake:echo") -> dict:
    agent, _ = db.create_agent("bot", "Bot")
    v = db.create_version(agent["id"], system_prompt="x", model=model)
    return db.update_agent(agent["id"], prod_version_id=v["id"])  # type: ignore[return-value]


def schedule(agent: dict, base: float, **cfg) -> dict:
    config = core.validate_config("schedule", {"cron": "0 9 * * *", "input_template": "Daily report for {date}",
                                               **cfg})
    return store.create(agent["id"], "schedule", config, secret=None, tokens={}, last_slot_at=base)


# ---------------------------------------------------------------- scheduler


async def test_schedule_fires_once_per_slot_and_survives_restart(mgr):
    """TRG6: a slot fires exactly once, even across a scheduler restart, with {date} rendered in the trigger zone."""
    agent = make_agent_row()
    t = schedule(agent, T(2026, 1, 5, 8, 59, 30), timezone="America/New_York", cron="0 4 * * *")
    clock = [T(2026, 1, 5, 8, 59, 50)]
    s = Scheduler(clock=lambda: clock[0], interval=0)
    assert await s.tick() == [] and mgr.started == []
    clock[0] = T(2026, 1, 5, 9, 0, 5)  # 04:00 EST
    fired = await s.tick()
    assert [f.status for f in fired] == ["started"] and len(mgr.started) == 1
    assert mgr.started[0]["input"] == "Daily report for 2026-01-05" and mgr.started[0]["source"] == "schedule"
    clock[0] += 15
    assert await s.tick() == []
    restarted = Scheduler(clock=lambda: clock[0], interval=0)
    assert await restarted.tick() == [] and len(mgr.started) == 1
    row = store.get(t["id"])
    assert row["last_run_id"] == mgr.started[0]["id"] and row["last_slot_at"] == T(2026, 1, 5, 9)


async def test_schedule_skips_while_previous_run_is_running(mgr):
    """TRG6: with skip_if_running (default) a slot is skipped and recorded while the last run is active."""
    agent = make_agent_row()
    t = schedule(agent, T(2026, 1, 5, 8), cron="0 * * * *")
    clock = [T(2026, 1, 5, 9, 0, 1)]
    s = Scheduler(clock=lambda: clock[0], interval=0)
    assert [f.status for f in await s.tick()] == ["started"]
    clock[0] = T(2026, 1, 5, 10, 0, 1)
    assert [f.status for f in await s.tick()] == ["skipped"]
    mgr.finish(mgr.started[0]["id"])
    clock[0] = T(2026, 1, 5, 11, 0, 1)
    assert [f.status for f in await s.tick()] == ["started"]
    statuses = [f["status"] for f in store.list_fires(t["id"])]
    assert statuses == ["started", "skipped", "started"]
    # overlapping allowed when configured
    store.update(t["id"], config={**t["config"], "skip_if_running": False})
    clock[0] = T(2026, 1, 5, 12, 0, 1)
    assert [f.status for f in await s.tick()] == ["started"] and len(mgr.started) == 3


async def test_schedule_skips_missed_slots_older_than_ten_minutes(mgr):
    """TRG6: slots more than 10 minutes late are skipped and recorded once; a late but recent slot still fires."""
    agent = make_agent_row()
    t = schedule(agent, T(2026, 1, 5, 9, 30))
    s = Scheduler(clock=lambda: T(2026, 1, 8, 9, 20), interval=0)  # slots on the 6th, 7th, 8th are all too late
    assert await s.tick() == [] and mgr.started == []
    fires = store.list_fires(t["id"])
    assert [f["status"] for f in fires] == ["missed"] and "Skipped 3 scheduled" in fires[0]["detail"]
    s.clock = lambda: T(2026, 1, 9, 9, 5)
    assert [f.status for f in await s.tick()] == ["started"]
    assert mgr.started[0]["input"] == "Daily report for 2026-01-09"


async def test_every_minute_schedule_after_long_downtime_fires_latest_slot_once(mgr):
    """TRG6: after days offline an every-minute schedule records the gap and fires only once, for the latest slot."""
    agent = make_agent_row()
    t = schedule(agent, T(2026, 1, 1), cron="* * * * *", input_template="tick {time}")
    s = Scheduler(clock=lambda: T(2026, 1, 4, 12, 0, 30), interval=0)
    assert [f.status for f in await s.tick()] == ["started"]
    assert mgr.started[0]["input"] == "tick 12:00"
    fires = store.list_fires(t["id"])
    assert sorted(f["status"] for f in fires) == ["missed", "started"]
    assert "1000+" in next(f for f in fires if f["status"] == "missed")["detail"]


async def test_disabled_schedule_does_not_fire(mgr):
    agent = make_agent_row()
    t = schedule(agent, T(2026, 1, 5, 8))
    store.update(t["id"], enabled=False)
    assert await Scheduler(clock=lambda: T(2026, 1, 5, 9, 0, 1), interval=0).tick() == []


async def test_schedule_respects_rate_limit_and_version_pin(mgr):
    """TRG5, TRG7: the per trigger rate limit applies to schedules; a pinned version runs instead of prod."""
    agent = make_agent_row()
    v2 = db.create_version(agent["id"], system_prompt="y", model="fake:echo")
    db.update_agent(agent["id"], prod_version_id=v2["id"])
    t = schedule(agent, T(2026, 1, 5, 8), cron="0 * * * *", version=1, max_runs_per_hour=1, skip_if_running=False)
    s = Scheduler(clock=lambda: T(2026, 1, 5, 9, 0, 1), interval=0)
    assert [f.status for f in await s.tick()] == ["started"] and mgr.started[0]["version_n"] == 1
    s.clock = lambda: T(2026, 1, 5, 10, 0, 1)
    assert [f.status for f in await s.tick()] == ["rate_limited"]
    assert "rate limit" in store.get(t["id"])["last_error"]


# ---------------------------------------------------------------- HTTP API


@pytest.fixture
def client(mgr):
    from forge.server.app import app

    with TestClient(app, client=LOCAL) as c:
        yield c


def remote():
    from forge.server.app import app

    return TestClient(app, client=REMOTE)


@pytest.fixture
def agent(client) -> dict:
    r = client.post("/v1/agents", json={"slug": "bot", "name": "Bot", "system_prompt": "x", "model": "fake:echo"})
    assert r.status_code == 200
    return r.json()


def gh(client, tid, event, payload, secret, *, sign=True, headers=None):
    body = json.dumps(payload).encode()
    h = {"X-GitHub-Event": event, "Content-Type": "application/json", **(headers or {})}
    if sign:
        h["X-Hub-Signature-256"] = github_signature(secret, body)
    return client.post(f"/v1/hooks/github/{tid}", content=body, headers=h)


def slack_post(client, tid, body: bytes, secret, *, ctype="application/x-www-form-urlencoded", stamp=None):
    stamp = stamp or str(int(time.time()))
    return client.post(f"/v1/hooks/slack/{tid}", content=body, headers={
        "Content-Type": ctype, "X-Slack-Request-Timestamp": stamp,
        "X-Slack-Signature": slack_signature(secret, stamp, body)})


ISSUE = {"action": "opened", "repository": {"full_name": "o/r"}, "sender": {"login": "alice", "type": "User"},
         "issue": {"number": 4, "title": "Crash on start", "body": "Stack trace", "html_url": "https://g/i/4",
                   "user": {"login": "alice"}}}


def test_trigger_crud_and_secrets_shown_once(client, agent):
    """TRG7: CRUD; the GitHub secret is returned only on create (and rotate); tokens are never returned."""
    bad = client.post("/v1/agents/bot/triggers", json={"kind": "schedule", "config": {"cron": "61 * * * *",
                                                                                      "input_template": "x"}})
    assert bad.status_code == 400 and "minute" in bad.json()["detail"]
    assert client.post("/v1/agents/bot/triggers", json={"kind": "schedule", "config": {"cron": "* * * * *"}}
                       ).status_code == 400
    assert client.post("/v1/agents/bot/triggers", json={"kind": "github", "config": {"version": 9}}).status_code == 400
    s = client.post("/v1/agents/bot/triggers", json={"kind": "schedule", "config": {
        "cron": "0 9 * * 1-5", "timezone": "Europe/Berlin", "input_template": "Report {date}"}}).json()
    assert s["kind"] == "schedule" and len(s["next_fire_times"]) == 3 and s["hook_path"] is None
    g = client.post("/v1/agents/bot/triggers", json={"kind": "github", "token": "ghp_secret_token"}).json()
    assert g["secret"].startswith("whsec_") and g["has_secret"] and g["has_token"]
    assert g["hook_path"] == f"/v1/hooks/github/{g['id']}"
    listed = client.get("/v1/agents/bot/triggers").json()
    assert [t["id"] for t in listed] == [s["id"], g["id"]]
    raw = json.dumps(listed) + client.get(f"/v1/triggers/{g['id']}").text
    assert g["secret"] not in raw and "ghp_secret_token" not in raw and '"secret"' not in raw
    p = client.patch(f"/v1/triggers/{g['id']}", json={"config": {"label": "forge"}, "enabled": False, "token": ""})
    assert p.status_code == 200
    p = p.json()
    assert p["config"]["label"] == "forge" and p["config"]["command"] == "/forge" and not p["enabled"]
    assert not p["has_token"]
    rot = client.post(f"/v1/triggers/{g['id']}/rotate-secret").json()["secret"]
    assert rot != g["secret"] and store.get(g["id"])["secret"] == rot
    assert client.delete(f"/v1/triggers/{s['id']}").json() == {"ok": True}
    assert client.get(f"/v1/triggers/{s['id']}").status_code == 404


def test_builder_routes_need_localhost_or_api_key(client, agent):
    """TRG7: tunneled or remote callers need the agent API key for trigger management."""
    t = client.post("/v1/agents/bot/triggers", json={"kind": "github"}).json()
    with remote() as r:
        assert r.get("/v1/agents/bot/triggers").status_code == 401
        assert r.get(f"/v1/triggers/{t['id']}/history").status_code == 401
        assert r.post(f"/v1/triggers/{t['id']}/test").status_code == 401
        key = {"Authorization": f"Bearer {agent['api_key']}"}
        assert r.get("/v1/agents/bot/triggers", headers=key).status_code == 200
    proxied = {"cf-connecting-ip": "203.0.113.7"}
    assert client.get("/v1/agents/bot/triggers", headers=proxied).status_code == 401
    share = {"X-Share-Token": agent["share_token"], **proxied}
    assert client.get("/v1/agents/bot/triggers", headers=share).status_code == 401


def test_github_hook_signature_events_and_rate_limit(client, agent, mgr):
    """TRG2, TRG5: unsigned and wrongly signed payloads get 401; ping 200; configured events 202 with a run;
    other events 204; the hook works through the tunnel (proxy headers) and is rate limited with 429."""
    t = client.post("/v1/agents/bot/triggers", json={"kind": "github", "config": {"max_runs_per_hour": 2}}).json()
    tid, secret = t["id"], t["secret"]
    assert gh(client, tid, "issues", ISSUE, secret, sign=False).status_code == 401
    assert gh(client, tid, "issues", ISSUE, "wrong").status_code == 401
    assert gh(client, "tr_000000000000", "issues", ISSUE, secret).status_code == 404
    assert gh(client, tid, "ping", {"zen": "x"}, secret).status_code == 200
    tunnel = {"cf-connecting-ip": "140.82.112.1", "x-forwarded-for": "140.82.112.1"}
    with remote() as r:
        res = gh(r, tid, "issues", ISSUE, secret, headers=tunnel)
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    assert res.json()["run_url"].endswith(f"/runs/{run_id}")
    run = db.get_run(run_id)
    assert run["source"] == "github" and "Crash on start" in run["input"] and "o/r" in run["input"]
    ignored = gh(client, tid, "push", {"ref": "refs/heads/main"}, secret)
    assert ignored.status_code == 204 and "not enabled" in ignored.headers["x-forge-ignored"]
    assert gh(client, tid, "issues", ISSUE, secret).status_code == 202
    limited = gh(client, tid, "issues", ISSUE, secret)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    client.patch(f"/v1/triggers/{tid}", json={"enabled": False})
    assert gh(client, tid, "issues", ISSUE, secret).status_code == 204
    hist = client.get(f"/v1/triggers/{tid}/history").json()
    assert [h["status"] for h in hist] == ["rate_limited", "started", "started"]
    assert hist[-1]["run"]["id"] == run_id


def test_github_comment_back_with_mock_api(client, agent, mgr, monkeypatch):
    """TRG2: with a token configured the final output is posted as an issue comment; failures are recorded."""
    calls: list[httpx.Request] = []
    status = [500, 201]

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(status.pop(0) if status else 403, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(core.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("FORGE_TRIGGER_API_HOSTS", "http://gh.test")  # the operator allows the mock API
    t = client.post("/v1/agents/bot/triggers", json={"kind": "github", "token": "ghp_x", "config": {
        "api_base": "http://gh.test/api"}}).json()
    res = gh(client, t["id"], "issues", ISSUE, t["secret"])
    run_id = res.json()["run_id"]
    mgr.finish(run_id, output="Fixed **it**")

    def fire_delivery():
        return store.list_fires(t["id"])[0]["delivery"]

    deadline = time.time() + 5
    while fire_delivery() is None and time.time() < deadline:
        time.sleep(0.02)
    assert fire_delivery() == "delivered"
    assert len(calls) == 2  # one retry after the 500
    assert str(calls[-1].url) == "http://gh.test/api/repos/o/r/issues/4/comments"
    assert calls[-1].headers["authorization"] == "Bearer ghp_x"
    body = json.loads(calls[-1].content)["body"]
    assert body.startswith("Fixed **it**") and run_id in body
    # second run: the API refuses (403, not retried) and the error is recorded on the trigger
    res = gh(client, t["id"], "issues", ISSUE, t["secret"])
    mgr.finish(res.json()["run_id"])
    deadline = time.time() + 5
    while fire_delivery() is None and time.time() < deadline:
        time.sleep(0.02)
    assert fire_delivery() == "failed"
    assert "HTTP 403" in store.get(t["id"])["last_error"]


def test_slack_slash_command_and_events(client, agent, mgr, monkeypatch):
    """TRG3: signature and replay window enforced; slash commands answer at once with an ephemeral message and the
    run link; url_verification echoes the challenge; app_mention starts a run once per event id."""
    t = client.post("/v1/agents/bot/triggers", json={"kind": "slack", "signing_secret": "sig"}).json()
    tid = t["id"]
    assert t["has_secret"] and "secret" not in t
    form = urlencode({"command": "/forge", "text": "summarize the week", "user_name": "bob",
                      "response_url": "https://hooks.slack.com/commands/1/2/3"}).encode()
    assert slack_post(client, tid, form, "nope").status_code == 401
    old = str(int(time.time()) - 400)
    assert slack_post(client, tid, form, "sig", stamp=old).status_code == 401
    started = time.monotonic()
    res = slack_post(client, tid, form, "sig")
    assert time.monotonic() - started < 3
    assert res.status_code == 200 and res.json()["response_type"] == "ephemeral"
    assert res.json()["text"].startswith("Working on it.") and "/runs/r_" in res.json()["text"]
    assert mgr.started[-1]["input"] == "summarize the week" and mgr.started[-1]["source"] == "slack"
    empty = slack_post(client, tid, urlencode({"command": "/forge", "text": ""}).encode(), "sig")
    assert "Add a task" in empty.json()["text"]
    ch = slack_post(client, tid, json.dumps({"type": "url_verification", "challenge": "abc"}).encode(), "sig",
                    ctype="application/json")
    assert ch.json() == {"challenge": "abc"}
    ev = {"type": "event_callback", "event_id": "Ev1", "event": {"type": "app_mention", "text": "<@U123> do it",
                                                                 "channel": "C1", "ts": "1.2", "user": "U9"}}
    body = json.dumps(ev).encode()
    assert slack_post(client, tid, body, "sig", ctype="application/json").json()["ok"]
    assert mgr.started[-1]["input"] == "do it"
    n = len(mgr.started)
    assert slack_post(client, tid, body, "sig", ctype="application/json").json().get("duplicate")
    bot = json.dumps({**ev, "event_id": "Ev2", "event": {**ev["event"], "bot_id": "B1"}}).encode()
    slack_post(client, tid, bot, "sig", ctype="application/json")
    assert len(mgr.started) == n


def test_test_endpoint_fires_with_sample_payload(client, agent, mgr):
    """TRG7: Test fires once with a sample payload and shows up in history marked as a test."""
    t = client.post("/v1/agents/bot/triggers", json={"kind": "github", "config": {
        "input_template": "Handle {repo} issue: {title}"}}).json()
    r = client.post(f"/v1/triggers/{t['id']}/test")
    assert r.status_code == 202 and r.json()["input"] == "Handle octo-org/hello-world issue: Sample issue"
    hist = client.get(f"/v1/triggers/{t['id']}/history").json()
    assert hist[0]["test"] and hist[0]["run"]["source"] == "github"
    s = client.post("/v1/agents/bot/triggers", json={"kind": "schedule", "config": {
        "cron": "@daily", "input_template": "Report for {date}"}}).json()
    r = client.post(f"/v1/triggers/{s['id']}/test").json()
    assert r["input"].startswith("Report for 20")


def test_hook_signature_uses_hmac_sha256(client, agent):
    """TRG2: the documented scheme is exactly HMAC-SHA256 hex with the shown secret."""
    t = client.post("/v1/agents/bot/triggers", json={"kind": "github"}).json()
    body = json.dumps({"zen": "z"}).encode()
    sig = "sha256=" + hmac.new(t["secret"].encode(), body, hashlib.sha256).hexdigest()
    r = client.post(f"/v1/hooks/github/{t['id']}", content=body, headers={"X-GitHub-Event": "ping",
                                                                          "X-Hub-Signature-256": sig})
    assert r.status_code == 200


# ---------------------------------------------------------------- integration: API base allowlist, budgets, approvals


def test_api_base_allowlist_on_create_update_and_call(client, agent, monkeypatch):
    """Security: per-trigger API bases are limited to https://api.github.com and https://slack.com unless the
    operator lists more origins in FORGE_TRIGGER_API_HOSTS; checked on create, on update and at call time."""
    from forge.triggers import github, slack
    from forge.triggers.security import api_base_allowed

    def create(kind: str, base: str):
        return client.post("/v1/agents/bot/triggers", json={"kind": kind, "config": {"api_base": base}})

    for kind, base in (("github", "https://evil.example"), ("github", "http://api.github.com"),
                       ("github", "https://api.github.com:8443"), ("github", "https://slack.com/api"),
                       ("github", "https://user@api.github.com"), ("slack", "https://slack.com.evil.io/api"),
                       ("slack", "http://127.0.0.1:9/api"), ("slack", "file:///etc/passwd")):
        r = create(kind, base)
        assert r.status_code == 400 and "FORGE_TRIGGER_API_HOSTS" in r.json()["detail"], (kind, base, r.text)
    assert create("github", "https://api.github.com").status_code == 200
    assert create("slack", "https://slack.com/api").status_code == 200
    ok = create("github", "https://api.github.com/").json()
    r = client.patch(f"/v1/triggers/{ok['id']}", json={"config": {"api_base": "https://attacker.test"}})
    assert r.status_code == 400

    monkeypatch.setenv("FORGE_TRIGGER_API_HOSTS", "ghe.example.com, http://127.0.0.1:9000")
    assert api_base_allowed("github", "https://ghe.example.com/api/v3")
    assert not api_base_allowed("github", "http://ghe.example.com/api/v3")  # bare host means https
    assert api_base_allowed("slack", "http://127.0.0.1:9000/api") and not api_base_allowed("slack", "http://127.0.0.1:9001")
    t = create("github", "https://ghe.example.com/api/v3").json()
    assert t["config"]["api_base"] == "https://ghe.example.com/api/v3"
    s = create("slack", "http://127.0.0.1:9000/api").json()

    monkeypatch.delenv("FORGE_TRIGGER_API_HOSTS")  # the operator narrows the list: stored bases stop working
    run = {"id": "r_x", "output": "hi", "status": "succeeded", "error": None}
    trow, srow = store.get(t["id"]), store.get(s["id"])
    trow["tokens"], srow["tokens"] = {"github_token": "ghp"}, {"bot_token": "xoxb"}
    ok_, err = asyncio.run(github.deliver(trow, run, {"repo": "o/r", "number": 1}))
    assert not ok_ and "not allowed" in err
    ok_, err = asyncio.run(slack.deliver_thread(srow, run, {"channel": "C", "thread_ts": "1"}))
    assert not ok_ and "not allowed" in err


def test_trigger_runs_respect_the_monthly_budget(client, agent, monkeypatch):
    """Governance: a trigger fire with the agent's monthly budget used up records a 402 fire error."""
    monkeypatch.setattr(runs, "get_manager", lambda m=runs.RunManager(): m)
    client.patch("/v1/agents/bot", json={"monthly_budget_usd": 0})
    t = client.post("/v1/agents/bot/triggers", json={"kind": "schedule", "config": {
        "cron": "0 9 * * *", "input_template": "daily"}}).json()
    r = client.post(f"/v1/triggers/{t['id']}/test", json={})
    assert r.status_code == 402 and "Monthly budget of $0.00 reached" in r.json()["detail"]
    [f] = store.list_fires(t["id"])
    assert f["status"] == "error" and f["detail"].startswith("402: Monthly budget") and f["run_id"] is None
    assert store.get(t["id"])["last_error"].startswith("402:")
    assert db.list_runs(agent["id"]) == []


async def test_trigger_run_pauses_for_approval_and_delivers_after(monkeypatch):
    """Governance: an ask rule pauses a trigger-started run like any other (awaiting_approval, a schedule sees it
    as still running); the GitHub comment is posted only after the reviewer decides."""
    from forge import governance
    from forge.policy import PRESETS

    monkeypatch.setenv("FORGE_EXECUTOR", "local")
    mgr = runs.RunManager()
    monkeypatch.setattr(runs, "get_manager", lambda: mgr)
    posted: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        posted.append(json.loads(req.content))
        return httpx.Response(201, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(core.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    a, _ = db.create_agent("risky", "Risky")
    v = db.create_version(a["id"], system_prompt="x", model="fake:risky", approval_policy={"rules": PRESETS["rm_rf"]})
    db.set_prod_version(a["id"], v["id"], "create")
    t = store.create(a["id"], "github", core.validate_config("github", {}), secret="s",
                     tokens={"github_token": "ghp"})
    fired = await core.fire(t, "clean", context={"deliver": "github_comment", "repo": "o/r", "number": 3})
    assert fired.status == "started"
    for _ in range(500):
        if db.get_run(fired.run_id)["status"] == "awaiting_approval":
            break
        await asyncio.sleep(0.01)
    assert db.get_run(fired.run_id)["status"] in core.ACTIVE == {"queued", "running", "awaiting_approval"}
    await asyncio.sleep(0.1)
    assert posted == [] and store.list_fires(t["id"])[0]["delivery"] is None
    [ap] = governance.list_approvals(fired.run_id)
    assert mgr.approvals.resolve(ap["id"], "approve", "", "ui")
    for _ in range(500):
        if store.list_fires(t["id"])[0]["delivery"]:
            break
        await asyncio.sleep(0.01)
    assert store.list_fires(t["id"])[0]["delivery"] == "delivered"
    assert "Removed the build directory." in posted[0]["body"]
    assert db.get_run(fired.run_id)["source"] == "github"
