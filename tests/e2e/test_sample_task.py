"""End-to-end sample tasks (the Generate button): the real server with `fake:grader` as the grader model.

`fake:grader` answers the sample task prompt with the first of FAKE_SAMPLE_TASKS not already in the request.
"""

from __future__ import annotations

import pytest

from forge.providers import FAKE_SAMPLE_TASKS

pytestmark = pytest.mark.e2e

REMOTE = {"X-Forwarded-For": "203.0.113.9"}  # the server then sees a remote client (no localhost bypass)


def test_builder_gets_fake_task_with_context_then_runs_it_and_next_one_differs(api, make_agent):
    """GEN1: the endpoint returns the fake task plus builder fields; running it succeeds; the next generation
    skips it because it is now a recent input."""
    agent = make_agent("gen", model="fake:echo", seed_files={"README.md": "# Demo\n", "src/app.py": "x = 1\n"})
    slug = agent["slug"]
    r = api.post(f"/agents/{slug}/sample-task", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == FAKE_SAMPLE_TASKS[0]["title"] and d["prompt"] == FAKE_SAMPLE_TASKS[0]["prompt"]
    assert d["version"] == 1 and d["context"]["excerpts"][0] == "README.md"
    run = api.post(f"/agents/{slug}/runs", json={"input": d["prompt"], "wait": True}).json()
    assert run["status"] == "succeeded"
    nxt = api.post(f"/agents/{slug}/sample-task", json={"version": 1}).json()
    assert nxt["prompt"] == FAKE_SAMPLE_TASKS[1]["prompt"]
    # with an API key from a remote client too
    key = {**REMOTE, "Authorization": f"Bearer {agent['api_key']}"}
    assert "context" in api.post(f"/agents/{slug}/sample-task", json={}, headers=key).json()


def test_share_token_works_hides_builder_fields_and_is_rate_limited(api, make_agent):
    """GEN2: a share token may generate (prod only), gets only title, prompt and rationale, and the 11th call
    within an hour is 429 with Retry-After; no token is 401."""
    agent = make_agent("genshare", model="fake:echo")
    slug, token = agent["slug"], agent["share_token"]
    assert api.post(f"/agents/{slug}/sample-task", json={}, headers=REMOTE).status_code == 401
    share = {**REMOTE, "X-Share-Token": token}
    for i in range(10):
        r = api.post(f"/agents/{slug}/sample-task", json={"version": 1, "hint": "short"}, headers=share)
        assert r.status_code == 200, (i, r.text)
        assert set(r.json()) == {"title", "prompt", "rationale"}
    r = api.post(f"/agents/{slug}/sample-task?share={token}", json={}, headers=REMOTE)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    # the builder is not limited by the share link's budget
    assert api.post(f"/agents/{slug}/sample-task", json={}).status_code == 200
