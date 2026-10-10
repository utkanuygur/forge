"""End-to-end tests for immutable versions, the prod pointer, share links and feedback."""

from __future__ import annotations

import pytest

from tests.e2e.helpers import events

pytestmark = pytest.mark.e2e

REMOTE = {"X-Forwarded-For": "203.0.113.9"}  # uvicorn trusts this from 127.0.0.1, so the server sees a remote client


def run(api, slug: str, **body) -> dict:
    r = api.post(f"/agents/{slug}/runs", json={"input": "hi", "wait": True, **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_versions_are_immutable_pinnable_and_promote_moves_prod(api, make_agent):
    """FR4, NFR2: creating v2 with a new prompt leaves v1 unchanged; a run can pin a version; promote moves the
    prod pointer used by unpinned runs, and promoting v1 again rolls back."""
    agent = make_agent("ver", model="fake:echo", system_prompt="prompt one")
    slug = agent["slug"]
    v1 = api.get(f"/agents/{slug}/versions/1").json()
    v2 = api.post(f"/agents/{slug}/versions", json={"system_prompt": "prompt two", "rationale": "try"}).json()
    assert v2["n"] == 2 and v2["system_prompt"] == "prompt two" and v2["parent_id"] == v1["id"]
    assert v2["model"] == "fake:echo" and v2["created_by"] == "human"
    assert api.get(f"/agents/{slug}/versions/1").json() == v1  # unchanged
    assert api.get(f"/agents/{slug}").json()["prod_version"] == 1
    assert api.patch(f"/agents/{slug}/versions/1", json={"system_prompt": "x"}).status_code == 405  # no edits

    unpinned = run(api, slug)
    assert unpinned["version_n"] == 1 and unpinned["version_id"] == v1["id"]
    pinned = run(api, slug, version=2)
    assert pinned["version_n"] == 2
    assert events(api, pinned["id"])[0]["payload"]["version_id"] == v2["id"]

    assert api.post(f"/agents/{slug}/promote", json={"version": 2}).json()["prod_version"] == 2
    assert api.get(f"/agents/{slug}/versions/prod").json()["id"] == v2["id"]
    assert run(api, slug)["version_n"] == 2
    assert api.post(f"/agents/{slug}/promote", json={"version": 1}).json()["prod_version"] == 1  # rollback
    assert run(api, slug)["version_n"] == 1
    assert api.post(f"/agents/{slug}/promote", json={"version": 99}).status_code == 404
    assert [v["n"] for v in api.get(f"/agents/{slug}").json()["versions"]] == [1, 2]


def test_share_token_can_run_and_give_feedback_but_not_read_config_or_evals(api, make_agent):
    """FR5: from a remote client a share token may start a run, read it and post feedback, but gets 401 on
    versions, evals, improvement and builder routes, and sees only share-sourced runs and public agent fields."""
    agent = make_agent("share", model="fake:echo")
    slug, token = agent["slug"], agent["share_token"]
    api_run = run(api, slug)  # a ui run that the share visitor must not see in listings
    share = {**REMOTE, "X-Share-Token": token}

    r = api.post(f"/agents/{slug}/runs", json={"input": "from the share page", "wait": True}, headers=share)
    assert r.status_code == 200, r.text
    shared = r.json()
    assert shared["source"] == "share" and shared["status"] == "succeeded"
    assert api.get(f"/runs/{shared['id']}", headers=share).status_code == 200
    q = api.post(f"/agents/{slug}/runs?share={token}", json={"input": "query param", "wait": True}, headers=REMOTE)
    assert q.status_code == 200 and q.json()["source"] == "share"

    fb = api.post(f"/runs/{shared['id']}/feedback", json={"rating": 1, "comment": "nice"}, headers=share)
    assert fb.status_code == 200 and fb.json()["source"] == "share"

    public = api.get(f"/agents/{slug}", headers=share).json()
    assert set(public) == {"id", "slug", "name", "description", "suggested_prompts"}
    listed = {x["id"] for x in api.get(f"/agents/{slug}/runs", headers=share).json()}
    assert shared["id"] in listed and api_run["id"] not in listed
    for path in ("", "/events.json", "/diff", "/artifacts", "/files"):  # nor by id: 404, the id is not confirmed
        assert api.get(f"/runs/{api_run['id']}{path}", headers=share).status_code == 404, path

    for method, path in [("GET", f"/agents/{slug}/versions/prod"), ("GET", f"/agents/{slug}/versions/1"),
                         ("POST", f"/agents/{slug}/versions"), ("GET", f"/agents/{slug}/evals"),
                         ("POST", f"/agents/{slug}/evals/run"), ("GET", f"/agents/{slug}/evals/results"),
                         ("POST", f"/agents/{slug}/improve"), ("GET", f"/agents/{slug}/improvements"),
                         ("POST", f"/agents/{slug}/promote"), ("PUT", f"/agents/{slug}/secrets"),
                         ("POST", f"/runs/{shared['id']}/save-as-eval"), ("GET", "/agents")]:
        body = {"version": 1} if path.endswith("/promote") else {}  # valid bodies, so only auth can refuse
        resp = api.request(method, path, json=body if method != "GET" else None, headers=share)
        assert resp.status_code == 401, (method, path, resp.status_code, resp.text)
    # a wrong token is rejected outright
    assert api.post(f"/agents/{slug}/runs", json={"input": "x"},
                    headers={**REMOTE, "X-Share-Token": "nope"}).status_code == 401


def test_feedback_is_stored_against_run_and_version(api, make_agent):
    """FR8: thumbs up/down with comments attach to the run, and through it to the version that produced it."""
    agent = make_agent("fb", model="fake:echo")
    slug = agent["slug"]
    v2 = api.post(f"/agents/{slug}/versions", json={"system_prompt": "second"}).json()
    r = run(api, slug, version=2)
    up = api.post(f"/runs/{r['id']}/feedback", json={"rating": 1, "comment": "good"}).json()
    down = api.post(f"/runs/{r['id']}/feedback", json={"rating": -1, "comment": "too terse"},
                    headers={"Authorization": f"Bearer {agent['api_key']}"}).json()
    assert up["run_id"] == down["run_id"] == r["id"] and up["id"].startswith("fb_")
    assert (up["source"], down["source"]) == ("ui", "api")
    assert api.post(f"/runs/{r['id']}/feedback", json={"rating": 0}).status_code == 422

    got = api.get(f"/runs/{r['id']}").json()
    assert got["version_id"] == v2["id"] and got["version_n"] == 2
    assert [(f["rating"], f["comment"]) for f in got["feedback"]] == [(1, "good"), (-1, "too terse")]
    listed = next(x for x in api.get(f"/agents/{slug}/runs").json() if x["id"] == r["id"])
    assert len(listed["feedback"]) == 2
