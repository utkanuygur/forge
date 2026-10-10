"""Share and API conversations against a real server, Docker sandboxes and fake models: a two turn share
conversation that carries its workspace, visitor isolation, share link limits, an API key conversation and a
Slack thread that continues one conversation."""

from __future__ import annotations

import json
import time
import uuid

import httpx
import pytest

from forge.triggers.security import slack_signature
from tests.e2e.helpers import poll
from tests.e2e.test_triggers import receiver, tapi, tserver  # noqa: F401  (fixtures)

pytestmark = pytest.mark.e2e

REMOTE = {"X-Forwarded-For": "203.0.113.7"}  # looks like the internet: no builder bypass


def share_headers(agent: dict, vid: str | None = None) -> dict:
    h = {**REMOTE, "X-Share-Token": agent["share_token"]}
    if vid:
        h["X-Forge-Visitor"] = vid
    return h


def test_two_turn_share_conversation_sees_the_first_turns_file(api, make_agent):
    agent = make_agent("share-conv", model="fake:session")
    s = api.post(f"/agents/{agent['slug']}/sessions", json={}, headers=share_headers(agent))
    assert s.status_code == 200, s.text
    vid = s.json()["visitor_id"]
    h = share_headers(agent, vid)
    sid = s.json()["id"]
    t1 = api.post(f"/sessions/{sid}/messages", json={"input": "create hello.py", "wait": True}, headers=h).json()
    assert t1["status"] == "succeeded" and t1["source"] == "share", t1
    t2 = api.post(f"/sessions/{sid}/messages", json={"input": "what is in hello.py?", "wait": True},
                  headers=h).json()
    assert t2["output"] == "Turn 2: hello.py contains `print('hello from forge')` (history had 1 earlier turn(s))."
    assert [f["path"] for f in api.get(f"/runs/{t2['id']}/files", headers=h).json()] == ["hello.py"]
    assert api.get(f"/runs/{t2['id']}/files/hello.py", headers=h).text == "print('hello from forge')\n"
    assert api.post(f"/runs/{t2['id']}/feedback", json={"rating": 1}, headers=h).status_code == 200

    # Visitor B: a fresh visitor id, and none of A's session or runs (404, not 401)
    b = api.post(f"/agents/{agent['slug']}/sessions", json={}, headers=share_headers(agent)).json()
    hb = share_headers(agent, b["visitor_id"])
    assert b["visitor_id"] != vid
    assert api.get(f"/sessions/{sid}", headers=hb).status_code == 404
    assert api.post(f"/sessions/{sid}/messages", json={"input": "x"}, headers=hb).status_code == 404
    for path in ("", "/events.json", "/files", "/artifacts", "/diff"):
        assert api.get(f"/runs/{t1['id']}{path}", headers=hb).status_code == 404, path
    assert [x["id"] for x in api.get(f"/agents/{agent['slug']}/sessions", headers=hb).json()] == [b["id"]]
    assert api.get(f"/agents/{agent['slug']}/runs", headers=hb).json() == []
    # the builder still sees everything
    assert api.get(f"/runs/{t1['id']}").json()["source"] == "share"


def test_share_limits_answer_429_with_retry_after(api, make_agent):
    agent = make_agent("share-lim", model="fake:echo")
    slug = agent["slug"]
    assert api.patch(f"/agents/{slug}", json={"share_limits": {"session_turns": 1, "sessions_per_visitor": 1,
                                                              "daily_turns": 2}}).status_code == 200
    s = api.post(f"/agents/{slug}/sessions", json={}, headers=share_headers(agent)).json()
    h = share_headers(agent, s["visitor_id"])
    assert api.post(f"/sessions/{s['id']}/messages", json={"input": "a", "wait": True}, headers=h).status_code == 200
    r = api.post(f"/sessions/{s['id']}/messages", json={"input": "b"}, headers=h)
    assert r.status_code == 429 and r.json()["limit"] == "session_turns" and "Retry-After" in r.headers
    r = api.post(f"/agents/{slug}/sessions", json={}, headers=h)
    assert r.status_code == 429 and r.json()["limit"] == "sessions_per_visitor" and int(r.headers["Retry-After"]) > 0
    other = api.post(f"/agents/{slug}/sessions", json={}, headers=share_headers(agent)).json()
    ho = share_headers(agent, other["visitor_id"])
    assert api.post(f"/sessions/{other['id']}/messages", json={"input": "c", "wait": True},
                    headers=ho).status_code == 200
    r = api.post(f"/agents/{slug}/runs", json={"input": "d"}, headers=ho)  # the third share turn today
    assert r.status_code == 429 and r.json()["detail"] == "You've reached today's limit for this link. Try again tomorrow."
    assert 0 < int(r.headers["Retry-After"]) <= 86400


def test_api_key_conversation(api, make_agent, server):
    agent = make_agent("api-conv", model="fake:session")
    key = {**REMOTE, "Authorization": f"Bearer {agent['api_key']}"}
    with httpx.Client(base_url=server.url + "/v1", timeout=120) as c:
        s = c.post(f"/agents/{agent['slug']}/sessions", json={"version": 1}, headers=key).json()
        assert s["channel"] == "api"
        c.post(f"/sessions/{s['id']}/messages", json={"input": "create", "wait": True}, headers=key)
        t2 = c.post(f"/sessions/{s['id']}/messages", json={"input": "read", "wait": True}, headers=key).json()
        assert t2["source"] == "api" and "hello from forge" in t2["output"]
        assert c.post(f"/agents/{agent['slug']}/sessions", json={}, headers=REMOTE).status_code == 401


def _mention(tapi: httpx.Client, trigger_id: str, text: str, ts: str, thread_ts: str | None) -> httpx.Response:
    ev = {"type": "app_mention", "text": f"<@UBOT> {text}", "user": "U1", "channel": "C9", "ts": ts}
    if thread_ts:
        ev["thread_ts"] = thread_ts
    body = json.dumps({"type": "event_callback", "event_id": f"Ev{uuid.uuid4().hex[:8]}", "team_id": "T1",
                       "event": ev}).encode()
    stamp = str(int(time.time()))
    return tapi.post(f"/hooks/slack/{trigger_id}", content=body, headers={
        "Content-Type": "application/json", "X-Slack-Request-Timestamp": stamp,
        "X-Slack-Signature": slack_signature("slack-sign", stamp, body), "X-Forwarded-For": "203.0.113.8"})


def test_slack_thread_replies_continue_one_conversation(tapi, receiver):  # noqa: F811
    base, calls = receiver
    slug = f"slk-{uuid.uuid4().hex[:6]}"
    assert tapi.post("/agents", json={"slug": slug, "name": "Slack", "system_prompt": "x",
                                      "model": "fake:session"}).status_code == 200
    tapi.patch(f"/agents/{slug}", json={"share_limits": {"session_turns": 2}})
    t = tapi.post(f"/agents/{slug}/triggers", json={"kind": "slack", "signing_secret": "slack-sign",
                                                   "token": "xoxb-test", "config": {"api_base": base}}).json()
    first = _mention(tapi, t["id"], "create hello.py", "100.1", None).json()
    run1 = poll(lambda: (r := tapi.get(f"/runs/{first['run_id']}").json())["status"] == "succeeded" and r,
                timeout=90, what="turn 1")
    poll(lambda: len(calls) >= 1 or None, timeout=30, what="the first thread reply")
    second = _mention(tapi, t["id"], "what is in hello.py?", "100.2", "100.1").json()
    run2 = poll(lambda: (r := tapi.get(f"/runs/{second['run_id']}").json())["status"] == "succeeded" and r,
                timeout=90, what="turn 2")
    assert run2["session_id"] == run1["session_id"] and run2["turn_index"] == 1 and run2["source"] == "slack"
    assert "history had 1 earlier turn(s)" in run2["output"]
    poll(lambda: len(calls) >= 2 or None, timeout=30, what="the second thread reply")
    assert all(c["body"]["thread_ts"] == "100.1" for c in calls)
    # the thread is full: no run, a note in the thread, 200 so Slack does not retry
    third = _mention(tapi, t["id"], "again", "100.3", "100.1")
    assert third.status_code == 200 and "limit of 2 messages" in third.json()["skipped"]
    poll(lambda: len(calls) >= 3 or None, timeout=30, what="the limit note")
    assert "limit of 2 messages" in calls[-1]["body"]["text"]
    # a new thread is a new conversation
    other = _mention(tapi, t["id"], "create hello.py", "200.1", None).json()
    run3 = poll(lambda: (r := tapi.get(f"/runs/{other['run_id']}").json())["status"] == "succeeded" and r,
                timeout=90, what="new thread")
    assert run3["session_id"] != run1["session_id"] and run3["turn_index"] == 0
