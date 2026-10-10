"""End-to-end Forge assistant: the real server streams SSE while the test answers confirmations, with
`fake:grader` (which answers like `fake:copilot` for the assistant's system prompt) as the model."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.e2e.helpers import wait_run

pytestmark = pytest.mark.e2e


def chat(api: httpx.Client, text: str, context: dict, *, answer: bool | None = None) -> tuple[str, list[dict]]:
    sid = api.post("/copilot/sessions", json={"context": context}).json()["id"]
    events: list[dict] = []
    with api.stream("POST", f"/copilot/sessions/{sid}/messages", json={"text": text, "context": context}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if not line.startswith("data:") or not (data := json.loads(line[5:].strip())):
                continue
            events.append(data)
            if data["type"] == "confirm_request" and answer is not None:
                c = api.post(f"/copilot/sessions/{sid}/confirm",
                             json={"confirm_id": data["payload"]["id"], "approve": answer})
                assert c.status_code == 200, c.text
    return sid, events


def payloads(events: list[dict], t: str) -> list[dict]:
    return [e["payload"] for e in events if e["type"] == t]


def test_add_three_eval_cases_after_confirmation(api, make_agent):
    """AS1: one confirmation card for three create_eval_case calls; approve creates exactly three cases."""
    slug = make_agent("cop")["slug"]
    ctx = {"page": f"/agents/{slug}/evals", "agent_slug": slug}
    sid, ev = chat(api, f"add 3 eval cases to {slug}", ctx, answer=True)
    req = payloads(ev, "confirm_request")
    assert len(req) == 1 and len(req[0]["items"]) == 3
    assert [r["ok"] for r in payloads(ev, "tool_result")] == [True, True, True]
    cases = api.get(f"/agents/{slug}/evals").json()
    assert sorted(c["name"] for c in cases) == [f"assistant-case-0-{i}" for i in (1, 2, 3)]
    assert payloads(ev, "final")[-1]["status"] == "done"
    s = api.get(f"/copilot/sessions/{sid}").json()
    assert s["running"] is False and any(m["type"] == "confirm_resolved" for m in s["messages"])


def test_deny_creates_nothing(api, make_agent):
    """AS2: denying the card returns an error result to the model and creates no case."""
    slug = make_agent("copdeny")["slug"]
    _, ev = chat(api, f"add two eval cases to {slug}", {"agent_slug": slug}, answer=False)
    assert payloads(ev, "confirm_resolved")[0]["approved"] is False
    assert api.get(f"/agents/{slug}/evals").json() == []
    assert "Nothing was added" in payloads(ev, "assistant")[-1]["text"]


def test_explain_a_failed_run(api, make_agent):
    """AS3: "why did run X fail" reads the run (a read tool, no confirmation) and quotes its error."""
    agent = make_agent("copfail", model="fake:error")
    run = api.post(f"/agents/{agent['slug']}/runs", json={"input": "do anything"}).json()
    run = wait_run(api, run["id"])
    assert run["status"] == "failed"
    _, ev = chat(api, f"why did run {run['id']} fail?", {"page": f"/runs/{run['id']}", "run_id": run["id"]})
    assert payloads(ev, "confirm_request") == []
    assert [c["tool"] for c in payloads(ev, "tool_call")] == ["get_run"]
    answer = payloads(ev, "assistant")[-1]["text"]
    assert "failed" in answer and "fake model failure" in answer
