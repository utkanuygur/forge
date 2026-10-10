"""Playground sessions and cancel against a real server, Docker sandboxes and fake models."""

from __future__ import annotations

import time

import pytest

from tests.e2e.helpers import events, poll

pytestmark = pytest.mark.e2e


def test_two_turn_session_carries_history_and_workspace(api, make_agent):
    """Turn 2 runs in a fresh container but sees turn 1's file and turn 1's conversation."""
    agent = make_agent("sess", model="fake:session")
    s = api.post(f"/agents/{agent['slug']}/sessions", json={}).json()
    assert s["is_draft"] is False and s["version_n"] == 1 and s["turns"] == []

    t1 = api.post(f"/sessions/{s['id']}/messages", json={"input": "create hello.py", "wait": True}).json()
    assert t1["status"] == "succeeded" and t1["source"] == "playground" and t1["turn_index"] == 0, t1
    t2 = api.post(f"/sessions/{s['id']}/messages", json={"input": "what is in hello.py?", "wait": True}).json()
    assert t2["status"] == "succeeded" and t2["turn_index"] == 1, t2
    assert t2["output"] == "Turn 2: hello.py contains `print('hello from forge')` (history had 1 earlier turn(s))."

    full = api.get(f"/sessions/{s['id']}").json()
    assert [t["id"] for t in full["turns"]] == [t1["id"], t2["id"]] and full["title"] == "create hello.py"
    assert full["running"] is False
    listed = api.get(f"/agents/{agent['slug']}/sessions").json()
    assert listed[0]["id"] == s["id"] and listed[0]["turn_count"] == 2

    assert full["turns"][1]["session_id"] == s["id"] and full["agent_slug"] == agent["slug"]
    # Each turn's diff is against its own starting workspace: turn 2 changed nothing.
    assert api.get(f"/runs/{t2['id']}/diff").text.strip() == ""
    # Playground runs with a saved version count like any run.
    assert t2["id"] in {r["id"] for r in api.get(f"/agents/{agent['slug']}/runs").json()}


def test_cancel_stops_a_running_run_quickly(api, make_agent):
    agent = make_agent("stop", model="fake:slow")
    run = api.post(f"/agents/{agent['slug']}/runs", json={"input": "wait"}).json()
    poll(lambda: any(e["type"] == "tool_call" for e in events(api, run["id"])) or None, timeout=60,
         what="the sleep to start")
    t0 = time.monotonic()
    r = api.post(f"/runs/{run['id']}/cancel")
    assert r.status_code == 200, r.text
    assert time.monotonic() - t0 < 15
    done = r.json()
    assert done["status"] == "cancelled" and done["error"] == "Stopped by user"
    evs = events(api, run["id"])
    assert any(e["type"] == "error" and e["payload"]["message"] == "Stopped by user" for e in evs)
    assert evs[-1]["type"] == "run_finished" and evs[-1]["payload"]["status"] == "cancelled"
    # Idempotent for a cancelled run; a run that finished otherwise cannot be cancelled.
    again = api.post(f"/runs/{run['id']}/cancel")
    assert again.status_code == 200 and again.json()["status"] == "cancelled"
    echo = make_agent("fin", model="fake:echo")
    finished = api.post(f"/agents/{echo['slug']}/runs", json={"input": "x", "wait": True}).json()
    assert api.post(f"/runs/{finished['id']}/cancel").status_code == 409


def test_cancel_ends_a_fake_loop_run(api, make_agent):
    agent = make_agent("loop", model="fake:loop")
    run = api.post(f"/agents/{agent['slug']}/runs", json={"input": "spin"}).json()
    poll(lambda: any(e["type"] == "tool_result" for e in events(api, run["id"])) or None, timeout=60,
         what="the loop to start")
    r = api.post(f"/runs/{run['id']}/cancel")
    assert r.status_code in (200, 409), r.text  # 409 only if the turn limit won the race
    if r.status_code == 200:
        assert r.json()["status"] == "cancelled"


def test_busy_session_rejects_a_second_message_then_continues_after_stop(api, make_agent):
    agent = make_agent("busy", model="fake:slow")
    s = api.post(f"/agents/{agent['slug']}/sessions", json={}).json()
    t1 = api.post(f"/sessions/{s['id']}/messages", json={"input": "first"}).json()
    assert api.post(f"/sessions/{s['id']}/messages", json={"input": "second"}).status_code == 409
    assert api.post(f"/runs/{t1['id']}/cancel").json()["status"] == "cancelled"
    t2 = api.post(f"/sessions/{s['id']}/messages", json={"input": "second"}).json()
    assert t2["turn_index"] == 1
    api.post(f"/runs/{t2['id']}/cancel")


def test_draft_session_is_excluded_from_metrics_and_can_be_saved(api, make_agent):
    agent = make_agent("draft", model="fake:echo")
    slug = agent["slug"]
    s = api.post(f"/agents/{slug}/sessions", json={"draft": {"system_prompt": "Draft prompt", "model": "fake:echo"}})
    assert s.status_code == 200, s.text
    s = s.json()
    assert s["is_draft"] and s["version_n"] is None and s["base_version_n"] == 1
    t = api.post(f"/sessions/{s['id']}/messages", json={"input": "hi", "wait": True}).json()
    assert t["status"] == "succeeded" and t["output"] == "echo: hi"
    assert t["id"] not in {r["id"] for r in api.get(f"/agents/{slug}/runs").json()}
    api.post(f"/runs/{t['id']}/feedback", json={"rating": -1, "comment": "draft only"})

    v = api.post(f"/sessions/{s['id']}/save-version", json={"rationale": "from playground"}).json()
    assert v["n"] == 2 and v["system_prompt"] == "Draft prompt" and v["rationale"] == "from playground"
    a = api.get(f"/agents/{slug}").json()
    assert a["prod_version"] == 1  # saving a draft never promotes
