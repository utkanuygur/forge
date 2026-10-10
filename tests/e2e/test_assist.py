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


def test_eval_case_and_eval_cases_create_nothing_until_added_and_checkers_fail_on_the_seed(api, make_agent):
    """GEN6: eval_case and eval_cases only return drafts; adding the batch creates exactly those cases, and the
    generated shell checkers FAIL on the untouched workspace when evaluated in the sandbox."""
    from tests.e2e.helpers import wait_evals

    agent = make_agent("gencase", model="fake:echo", seed_files={"README.md": "# Demo\n"})
    slug = agent["slug"]
    one = api.post("/assist/eval_case", json={"slug": slug, "input": {"split": "holdout", "checker_type": "shell"}})
    assert one.status_code == 200, one.text
    assert one.json()["result"]["split"] == "holdout" and one.json()["result"]["checker_type"] == "shell"
    batch = api.post("/assist/eval_cases", json={"slug": slug, "input": {"n": 3, "holdout": 1,
                                                                         "checker_type": "shell"}}).json()
    cases = batch["result"]["cases"]
    assert [c["split"] for c in cases] == ["train", "train", "holdout"]
    assert api.get(f"/agents/{slug}/evals").json() == []  # nothing saved by generating
    for c in cases:
        body = {k: c[k] for k in ("name", "input", "checker_type", "checker", "split")}
        assert api.post(f"/agents/{slug}/evals", json=body).status_code == 200
    assert sorted(c["name"] for c in api.get(f"/agents/{slug}/evals").json()) == sorted(c["name"] for c in cases)
    nxt = api.post("/assist/eval_case", json={"slug": slug, "input": {"checker_type": "shell"}}).json()["result"]
    assert nxt["name"] not in {c["name"] for c in cases}  # existing cases are avoided
    api.post(f"/agents/{slug}/evals/run", json={"version": 1})
    res = wait_evals(api, slug, 1, 3)
    assert all(not r["passed"] and "FAIL:" in r["reason"] for r in res["results"]), res["results"]


def test_eval_case_from_run_uses_its_input_and_feedback_then_save_as_eval(api, make_agent):
    """GEN6: from_run_id drafts a case from a run and its feedback (input verbatim); saving it is a separate call."""
    agent = make_agent("genrun", model="fake:echo")
    slug = agent["slug"]
    run = api.post(f"/agents/{slug}/runs", json={"input": "Explain how the workspace is laid out.",
                                                 "wait": True}).json()
    api.post(f"/runs/{run['id']}/feedback", json={"rating": -1, "comment": "It skipped the README"})
    d = api.post("/assist/eval_case", json={"slug": slug, "input": {"from_run_id": run["id"], "split": "holdout",
                                                                    "checker_type": "judge"}}).json()
    r = d["result"]
    assert r["input"] == "Explain how the workspace is laid out." and r["split"] == "holdout"
    assert "reviewer feedback" in r["checker"] and d["diagnostics"]["from_run_id"] == run["id"]
    assert api.get(f"/agents/{slug}/evals").json() == []
    other = make_agent("genother", model="fake:echo")
    bad = api.post("/assist/eval_case", json={"slug": other["slug"], "input": {"from_run_id": run["id"]}})
    assert bad.status_code == 400  # a run of another agent
    saved = api.post(f"/runs/{run['id']}/save-as-eval",
                     json={k: r[k] for k in ("name", "checker_type", "checker", "split")}).json()
    assert saved["name"] == r["name"] and saved["checker"] == r["checker"]
    assert [c["name"] for c in api.get(f"/agents/{slug}/evals").json()] == [r["name"]]


def test_cron_from_plain_english_with_next_fire_times(api, make_agent):
    """GEN7: cron turns plain English into an expression the schedule trigger accepts, with 3 next fire times."""
    import time

    agent = make_agent("gencron", model="fake:echo")
    d = api.post("/assist/cron", json={"slug": agent["slug"], "input": {"text": "every monday at 5pm",
                                                                        "timezone": "Europe/Istanbul"}}).json()
    r = d["result"]
    assert r["cron"] == "0 17 * * 1" and r["timezone"] == "Europe/Istanbul" and len(r["next_fire_times"]) == 3
    assert all(t > time.time() for t in r["next_fire_times"])
    t = api.post(f"/agents/{agent['slug']}/triggers", json={"kind": "schedule", "config": {
        "cron": r["cron"], "timezone": r["timezone"], "input_template": "Weekly check"}})
    assert t.status_code == 200, t.text
    assert api.post("/assist/cron", json={"input": {"text": "hourly"}}).json()["result"]["cron"] == "0 * * * *"
