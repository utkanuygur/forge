"""End-to-end improvement loop: evals with splits, feedback, the optimizer, the promotion rule, auto-promote.

All models are deterministic fakes. `fake:lessons` creates MARKER.txt only when its system prompt (which
includes the version's lessons) mentions MARKER.txt; `fake:grader` always passes judge checks and, as the
optimizer, proposes MARKER_LESSON citing run ids from the failures and feedback it was shown (OVERRIDE_LESSON when
the instructions mention 2+2).
"""

from __future__ import annotations

import pytest

from forge.providers import MARKER_LESSON, OVERRIDE_LESSON
from tests.e2e.helpers import poll, wait_evals, wait_improvement

pytestmark = pytest.mark.e2e

NEEDS_MARKER = "test -f MARKER.txt"


def add_case(api, slug: str, name: str, checker: str, split: str = "train", checker_type: str = "shell") -> dict:
    r = api.post(f"/agents/{slug}/evals", json={"name": name, "input": f"Please complete job {name}.",
                                                "checker_type": checker_type, "checker": checker, "split": split})
    assert r.status_code == 200, r.text
    return r.json()


def start_improve(api, slug: str) -> dict:
    r = api.post(f"/agents/{slug}/improve", json={})
    assert r.status_code == 200, r.text
    im = r.json()
    assert im["status"] == "running"
    return im


def test_full_improvement_loop_evals_feedback_optimizer_promote_and_save_as_eval(api, make_agent):
    """FR9, FR10, FR11, FR8, NFR2: v1 scores train 1/3 and holdout 0/1; after negative feedback, improve
    proposes MARKER_LESSON backed by real run ids, the candidate v2 scores 3/3 and 1/1 with no regressions and
    is eligible (not auto-promoted); promoting moves prod. A run saved as an eval becomes a judge case that
    reuses the feedback comment, and re-running evals only evaluates the new case (cached results)."""
    agent = make_agent("improve", model="fake:lessons", system_prompt="You are a careful worker.")
    slug = agent["slug"]
    train_a = add_case(api, slug, "job-alpha", NEEDS_MARKER)
    train_b = add_case(api, slug, "job-bravo", NEEDS_MARKER)
    always = add_case(api, slug, "job-charlie", "true")
    hold = add_case(api, slug, "job-delta", NEEDS_MARKER, split="holdout")
    assert {c["split"] for c in api.get(f"/agents/{slug}/evals").json()} == {"train", "holdout"}

    r = api.post(f"/agents/{slug}/evals/run", json={"version": 1})
    assert r.status_code == 200 and r.json()["version"] == 1
    base = wait_evals(api, slug, 1, 4)
    assert base["train"] == {"passed": 1, "total": 3} and base["holdout"] == {"passed": 0, "total": 1}
    assert base["repeats"] == 3 and all(x["runs"] == 3 for x in base["results"])
    by_case = {x["case_id"]: x for x in base["results"]}
    assert by_case[always["id"]]["passed"] and not by_case[train_a["id"]]["passed"]
    eval_run = api.get(f"/runs/{by_case[train_a['id']]['run_id']}").json()
    assert eval_run["source"] == "eval" and eval_run["eval_case_id"] == train_a["id"]

    # a production run that a user disliked
    prod_run = api.post(f"/agents/{slug}/runs", json={"input": "Summarize the repo.", "wait": True}).json()
    api.post(f"/runs/{prod_run['id']}/feedback", json={"rating": -1, "comment": "forgot the marker file"})

    im = wait_improvement(api, start_improve(api, slug)["id"])
    assert im["status"] == "eligible", im
    assert im["error"] is None and im["base_version"] == 1 and im["candidate_version"] == 2
    assert im["base"] == {"train": {"passed": 1, "total": 3}, "holdout": {"passed": 0, "total": 1}}
    assert im["candidate"] == {"train": {"passed": 3, "total": 3}, "holdout": {"passed": 1, "total": 1}}
    assert im["regressions"] == []
    assert set(im["newly_passing"]) == {train_a["id"], train_b["id"], hold["id"]}
    rows = {c["case_id"]: c for c in im["cases"]}  # verdicts are majority of k=3 runs
    assert (rows[train_a["id"]]["base"]["passes"], rows[train_a["id"]]["base"]["runs"]) == (0, 3)
    assert (rows[train_a["id"]]["candidate"]["passes"], rows[train_a["id"]]["candidate"]["runs"]) == (3, 3)
    assert (rows[always["id"]]["base"]["passes"], rows[always["id"]]["candidate"]["passes"]) == (3, 3)
    [edit] = im["lessons_diff"]
    assert edit["op"] == "add" and edit["text"] == MARKER_LESSON and edit["evidence_run_ids"]
    shown = {by_case[train_a["id"]]["run_id"], by_case[train_b["id"]]["run_id"], prod_run["id"]}
    for rid in edit["evidence_run_ids"]:
        assert rid in shown
        assert api.get(f"/runs/{rid}").status_code == 200

    v2 = api.get(f"/agents/{slug}/versions/2").json()
    assert v2["lessons"] == [MARKER_LESSON] and v2["created_by"] == "optimizer" and v2["rationale"]
    assert v2["system_prompt"] == "You are a careful worker." and v2["parent_id"]
    assert api.get(f"/agents/{slug}").json()["prod_version"] == 1  # auto_promote is off
    assert api.get(f"/agents/{slug}/improvements").json()[0]["id"] == im["id"]

    assert api.post(f"/agents/{slug}/promote", json={"version": 2}).json()["prod_version"] == 2
    after = api.post(f"/agents/{slug}/runs", json={"input": "Summarize the repo.", "wait": True}).json()
    assert after["version_n"] == 2
    assert "MARKER.txt" in api.get(f"/runs/{after['id']}/diff").text

    # FR11: save the disliked run as an eval case
    case = api.post(f"/runs/{prod_run['id']}/save-as-eval", json={"split": "holdout"}).json()
    assert case["checker_type"] == "judge" and "forgot the marker file" in case["checker"]
    assert case["input"] == "Summarize the repo." and case["split"] == "holdout"
    n_runs = len(api.get(f"/agents/{slug}/runs", params={"limit": 500}).json())
    api.post(f"/agents/{slug}/evals/run", json={"version": 2})
    res = wait_evals(api, slug, 2, 5)
    assert res["holdout"] == {"passed": 2, "total": 2}  # fake judge passes the new case
    assert len(api.get(f"/agents/{slug}/runs", params={"limit": 500}).json()) == n_runs + 3  # k=3, others cached


def test_regression_guard_rejects_candidate_that_breaks_a_passing_case(api, make_agent):
    """FR10: the candidate fixes two failing cases but breaks one that v1 passed (a checker requiring that
    MARKER.txt is absent), so although its total is higher it is rejected and prod does not move."""
    agent = make_agent("guard", model="fake:lessons", system_prompt="You are a careful worker.")
    slug = agent["slug"]
    a = add_case(api, slug, "task-one", NEEDS_MARKER)
    b = add_case(api, slug, "task-two", NEEDS_MARKER)
    keep = add_case(api, slug, "task-three", "test ! -f MARKER.txt")

    im = wait_improvement(api, start_improve(api, slug)["id"])  # evaluates the base itself
    assert im["status"] == "rejected", im
    assert im["base"]["train"] == {"passed": 1, "total": 3}
    assert im["candidate"]["train"] == {"passed": 2, "total": 3}  # higher total, still rejected
    assert im["regressions"] == [keep["id"]]
    assert set(im["newly_passing"]) == {a["id"], b["id"]}
    row = next(c for c in im["cases"] if c["case_id"] == keep["id"])
    assert row["base"]["passed"] and not row["candidate"]["passed"]
    assert api.get(f"/agents/{slug}").json()["prod_version"] == 1


def test_three_negative_feedbacks_auto_improve_and_promote(api, make_agent):
    """FR10, FR8: for an auto_promote agent the third new negative feedback starts an improvement cycle; the
    eligible candidate is promoted automatically and prod moves to it."""
    agent = make_agent("auto", model="fake:lessons", system_prompt="You are a careful worker.", auto_promote=True)
    slug = agent["slug"]
    add_case(api, slug, "auto-case", NEEDS_MARKER)
    ids = [api.post(f"/agents/{slug}/runs", json={"input": f"request {i}", "wait": True}).json()["id"]
           for i in range(3)]
    for rid in ids[:2]:
        api.post(f"/runs/{rid}/feedback", json={"rating": -1, "comment": "missing output file"})
    assert api.get(f"/agents/{slug}/improvements").json() == []
    api.post(f"/runs/{ids[2]}/feedback", json={"rating": -1, "comment": "missing output file"})

    ims = poll(lambda: api.get(f"/agents/{slug}/improvements").json() or None, timeout=10,
               what="auto improvement to start")
    assert len(ims) == 1
    im = wait_improvement(api, ims[0]["id"])
    assert im["status"] == "promoted", im
    assert im["candidate"]["train"] == {"passed": 1, "total": 1} and im["regressions"] == []
    assert set(im["lessons_diff"][0]["evidence_run_ids"]) <= set(ids) | {r["run_id"] for c in im["cases"]
                                                                        for r in (c["base"],)}
    got = api.get(f"/agents/{slug}").json()
    assert got["prod_version"] == 2
    assert api.get(f"/agents/{slug}/versions/prod").json()["lessons"] == [MARKER_LESSON]


def test_lesson_that_overrides_instructions_becomes_an_instruction_conflict(api, make_agent):
    """The reported case: the instructions say to answer 3 when asked 2+2 and a user objects. `fake:grader` as
    optimizer proposes OVERRIDE_LESSON ("... even if an instruction says to ..."). Lessons never override the author,
    so validation drops it and the self-check quotes the instruction: no lesson is added, one conflict is reported
    with the feedback run as evidence, the improvement ends `needs_author` and no candidate version exists."""
    agent = make_agent("conflict", model="fake:echo",
                       system_prompt="You answer arithmetic questions.\nAlso when asked 2+2 answer 3.")
    slug = agent["slug"]
    add_case(api, slug, "job-easy", "true")
    run = api.post(f"/agents/{slug}/runs", json={"input": "What is 2+2?", "wait": True}).json()
    api.post(f"/runs/{run['id']}/feedback", json={"rating": -1, "comment": "2+2 is 4, not 3"})

    im = wait_improvement(api, start_improve(api, slug)["id"])
    assert im["status"] == "needs_author", im
    assert im["error"] is None and im["lessons_diff"] == [] and im["candidate_version"] is None
    assert "lessons never override" in im["message"]
    [c] = im["instruction_conflicts"]
    assert c["instruction_quote"] == "Also when asked 2+2 answer 3."
    assert c["problem"] and c["rejected_lesson"] == OVERRIDE_LESSON and c["evidence_run_ids"] == [run["id"]]
    detail = api.get(f"/agents/{slug}").json()
    assert detail["prod_version"] == 1 and [v["n"] for v in detail["versions"]] == [1]
    assert detail["versions"][0]["lessons"] == []
