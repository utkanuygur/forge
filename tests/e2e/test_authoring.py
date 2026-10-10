"""End-to-end authoring: drafting an agent from a description, and pruning lessons that never helped.

Models are deterministic fakes: `fake:grader` returns FAKE_DRAFT for the drafting prompt; `fake:lessons` creates
MARKER.txt only when its system prompt (which includes the lessons) mentions MARKER.txt.
"""

from __future__ import annotations

import uuid

import pytest

from forge.providers import FAKE_DRAFT, MARKER_LESSON
from tests.e2e.helpers import wait_improvement
from tests.e2e.test_improvement import NEEDS_MARKER, add_case

pytestmark = pytest.mark.e2e

USELESS = "Keep your final reply under five sentences."


def test_draft_then_create_agent_with_selected_cases(api):
    """AUTH6, AUTH7: the draft endpoint returns the fake draft without creating anything; creating from it with
    three of the four starter cases makes the agent with the drafted config and exactly those cases."""
    before = {a["slug"] for a in api.get("/agents").json()}
    r = api.post("/agent-drafts", json={"description": "Triage incoming GitHub issues for our repo.",
                                        "examples": ["Triage issue #3"], "constraints": "Never close issues."})
    assert r.status_code == 200, r.text
    d = r.json()
    assert {a["slug"] for a in api.get("/agents").json()} == before
    assert d["name"] == FAKE_DRAFT["name"] and d["network"] is True and d["network_reason"]
    assert d["suggested_secrets"][0]["name"] == "GITHUB_TOKEN"
    assert d["slug"].startswith("issue-triager") and d["slug"] not in before

    slug = f"triager-{uuid.uuid4().hex[:6]}"
    chosen = [c for c in d["eval_cases"] if c["name"] != "feature-request"]
    r = api.post("/agents", json={"slug": slug, "name": d["name"], "description": d["description"],
                                  "system_prompt": d["system_prompt"], "tools": d["tools"], "network": d["network"],
                                  "eval_cases": chosen})
    assert r.status_code == 200, r.text
    v1 = api.get(f"/agents/{slug}/versions/1").json()
    assert v1["system_prompt"] == FAKE_DRAFT["system_prompt"] and v1["tools"] == FAKE_DRAFT["tools"]
    assert v1["network"] is True
    cases = api.get(f"/agents/{slug}/evals").json()
    assert sorted(c["name"] for c in cases) == sorted(c["name"] for c in chosen)
    assert {c["split"] for c in cases} == {"train", "holdout"}


def _agent_with_lessons(make_agent, api, prefix: str, lessons: list[str]) -> str:
    agent = make_agent(prefix, model="fake:lessons", system_prompt="You are a careful worker.", lessons=lessons)
    slug = agent["slug"]
    add_case(api, slug, f"{prefix}-a", NEEDS_MARKER)
    add_case(api, slug, f"{prefix}-b", NEEDS_MARKER)
    add_case(api, slug, f"{prefix}-h", NEEDS_MARKER, split="holdout")
    return slug


def test_prune_removes_useless_lesson_keeps_marker_and_is_eligible(api, make_agent):
    """PRUNE6, PRUNE7, PRUNE8: ablation shows MARKER_LESSON is needed (train 2/2 to 0/2) and USELESS is not
    (kept 2/2); the one candidate drops USELESS, keeps MARKER, ties base on train and holdout with zero
    regressions and is eligible under the pruning rule. Train results are reused from the identical ablation
    version, and provenance records the verdicts."""
    slug = _agent_with_lessons(make_agent, api, "prune", [MARKER_LESSON, USELESS])
    r = api.post(f"/agents/{slug}/prune", json={})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "prune" and r.json()["status"] == "running"
    assert api.post(f"/agents/{slug}/prune", json={}).status_code == 409
    im = wait_improvement(api, r.json()["id"])
    assert im["status"] == "eligible", im
    assert im["kind"] == "prune" and im["regressions"] == [] and im["error"] is None
    assert im["base"] == im["candidate"] == {"train": {"passed": 2, "total": 2}, "holdout": {"passed": 1, "total": 1}}
    [removal] = im["lessons_diff"]
    assert removal["op"] == "remove" and removal["text"] == USELESS
    assert removal["rationale"] == "removing this lesson kept train at 2/2" and len(removal["evidence_run_ids"]) == 2
    verdicts = {a["lesson"]: a for a in im["ablations"]}
    assert verdicts[MARKER_LESSON]["verdict"] == "helpful" and verdicts[MARKER_LESSON]["train"]["passed"] == 0
    assert verdicts[USELESS]["verdict"] == "unhelpful"

    cand = api.get(f"/agents/{slug}/versions/{im['candidate_version']}").json()
    assert cand["lessons"] == [MARKER_LESSON] and cand["created_by"] == "optimizer"
    assert cand["lesson_meta"][MARKER_LESSON]["pruning"]["verdict"] == "helpful"
    ablation = api.get(f"/agents/{slug}/versions/{verdicts[USELESS]['version']}").json()
    assert ablation["rationale"] == "ablation" and ablation["lessons"] == [MARKER_LESSON]
    assert api.get(f"/agents/{slug}").json()["prod_version"] == 1  # never promoted automatically
    cand_runs = {c["candidate"]["run_id"] for c in im["cases"] if c["split"] == "train"}
    assert cand_runs == set(removal["evidence_run_ids"])  # reused, not re-run
    prod = api.get(f"/agents/{slug}/versions/1").json()
    assert prod["lesson_meta"][MARKER_LESSON]["pruning"]["evidence"].startswith("removing this lesson dropped")
    assert api.get(f"/agents/{slug}/improvements").json()[0]["kind"] == "prune"


def test_prune_candidate_with_regression_is_rejected(api, make_agent):
    """PRUNE9: two lessons that each mention MARKER.txt are each unhelpful alone (the other still triggers it),
    but removing both breaks every case, so the candidate regresses and is rejected; prod stays."""
    slug = _agent_with_lessons(make_agent, api, "prunereg", [MARKER_LESSON, "Mention MARKER.txt in your reply."])
    im = wait_improvement(api, api.post(f"/agents/{slug}/prune", json={}).json()["id"])
    assert im["status"] == "rejected", im
    assert len(im["lessons_diff"]) == 2
    assert im["candidate"]["train"] == {"passed": 0, "total": 2} and len(im["regressions"]) == 3
    assert api.get(f"/agents/{slug}/versions/{im['candidate_version']}").json()["lessons"] == []
    assert api.get(f"/agents/{slug}").json()["prod_version"] == 1


def test_prune_with_k3_uses_majority_verdicts_and_meters_the_job(api, make_agent):
    """PRUNE11, OBS2: with k=3 every version in a prune job (base, each ablation, candidate) runs each case 3 times
    on the repeated-eval path. `fake:flaky` passes 2 of 3 runs per version (each version's system prompt differs,
    so each has its own counter), so every train verdict is a 2/3 majority pass; ablations run train cases only;
    every eval run is linked to the prune job in the cost breakdown."""
    from tests.e2e.test_repeats import FLAKY, eval_runs

    agent = make_agent("prunek3", model="fake:flaky", system_prompt=f"Flaky pruner {uuid.uuid4().hex[:6]}, k=3.",
                       lessons=["Lesson one: be brief.", "Lesson two: be polite."])
    slug = agent["slug"]
    assert agent["eval_repeats"] == 3
    train = add_case(api, slug, "prunek3-a", FLAKY)
    add_case(api, slug, "prunek3-h", FLAKY, split="holdout")
    im = wait_improvement(api, api.post(f"/agents/{slug}/prune", json={}).json()["id"])
    assert im["status"] == "eligible", im
    verdicts = {a["lesson"]: a for a in im["ablations"]}
    assert {a["verdict"] for a in verdicts.values()} == {"unhelpful"}
    assert all(a["base_train"] == a["train"] == {"passed": 1, "total": 1} for a in verdicts.values())
    for a in verdicts.values():
        res = api.get(f"/agents/{slug}/evals/results", params={"version": a["version"]}).json()
        assert [(r["case_id"], r["passed"], r["passes"], r["runs"]) for r in res["results"]] == \
            [(train["id"], True, 2, 3)]  # train only, majority of 3
    assert all((c["base"]["passes"], c["base"]["runs"]) == (2, 3) for c in im["cases"])
    assert all((c["candidate"]["passes"], c["candidate"]["runs"]) == (2, 3) for c in im["cases"])
    runs = eval_runs(api, slug)
    assert len(runs) == 6 + 2 * 3 + 6  # base (2 cases), two ablations (train only), candidate (2 cases)
    costs = api.get(f"/agents/{slug}/metrics", params={"days": 1}).json()["cost"]["improvements"]
    [row] = [c for c in costs if c["id"] == im["id"]]
    assert row["kind"] == "prune" and row["eval_runs"] == len(runs)
