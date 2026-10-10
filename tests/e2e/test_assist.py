"""End-to-end assist (Generate buttons): the real server with `fake:grader` as the grader model.

For kind "task", `fake:grader` returns the first of FAKE_TASKS whose prompt is not already in the request.
"""

from __future__ import annotations

import pytest

from forge.assist.kinds.task import FAKE_TASKS

pytestmark = pytest.mark.e2e

REMOTE = {"X-Forwarded-For": "203.0.113.9"}  # the server then sees a remote client (no localhost bypass)


def test_builder_gets_fake_task_with_diagnostics_then_runs_it_and_next_one_differs(api, make_agent):
    """GEN1 GEN4: assist/task returns the fake task plus builder fields; running it succeeds; the next generation
    skips it because it is now a recent input; an API key from a remote client works too."""
    agent = make_agent("gen", model="fake:echo", seed_files={"README.md": "# Demo\n", "src/app.py": "x = 1\n"})
    slug = agent["slug"]
    r = api.post("/assist/task", json={"slug": slug})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["result"] == {"title": FAKE_TASKS[0]["title"], "prompt": FAKE_TASKS[0]["prompt"]}
    assert d["version"] == 1 and d["diagnostics"]["excerpts"][0] == "README.md"
    run = api.post(f"/agents/{slug}/runs", json={"input": d["result"]["prompt"], "wait": True}).json()
    assert run["status"] == "succeeded"
    nxt = api.post(f"/agents/{slug}/sample-task", json={"version": 1}).json()  # the flat alias
    assert nxt["prompt"] == FAKE_TASKS[1]["prompt"] and "context" in nxt
    key = {**REMOTE, "Authorization": f"Bearer {agent['api_key']}"}
    assert "diagnostics" in api.post("/assist/task", json={"slug": slug}, headers=key).json()


def test_share_token_works_hides_builder_fields_and_is_rate_limited(api, make_agent):
    """GEN4: a share token may generate (prod only), gets only result and rationale, and the 11th call within an
    hour (across the endpoint and its alias) is 429 with Retry-After; no token is 401."""
    agent = make_agent("genshare", model="fake:echo")
    slug, token = agent["slug"], agent["share_token"]
    assert api.post("/assist/task", json={"slug": slug}, headers=REMOTE).status_code == 401
    share = {**REMOTE, "X-Share-Token": token}
    for i in range(10):
        if i % 2:
            r = api.post("/assist/task", json={"slug": slug, "version": 1}, headers=share)
            assert r.status_code == 200 and set(r.json()) == {"result", "rationale"}, (i, r.text)
        else:
            r = api.post(f"/agents/{slug}/sample-task", json={"hint": "short"}, headers=share)
            assert r.status_code == 200 and set(r.json()) == {"title", "prompt", "rationale"}, (i, r.text)
    r = api.post(f"/assist/task?share={token}", json={"slug": slug}, headers=REMOTE)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert api.post("/assist/task", json={"slug": slug}).status_code == 200  # the builder is not limited
