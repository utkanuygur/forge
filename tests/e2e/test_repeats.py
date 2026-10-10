"""End-to-end repeated evals: each case runs k times per version and passes on a strict majority (FR9, FR10).

`fake:flaky` creates FLAKY.txt on the 1st, 3rd, 5th, ... run of a given (system prompt, input) and not on
the others, so with k=3 a `test -f FLAKY.txt` case passes 2/3 (majority pass) and with k=2 it passes 1/2
(a tie, which fails). Each test uses its own system prompt so the server-wide counters do not interact.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import wait_evals, wait_improvement

pytestmark = pytest.mark.e2e

FLAKY = "test -f FLAKY.txt"


def add_case(api, slug: str, name: str, checker: str, split: str = "train") -> dict:
    r = api.post(f"/agents/{slug}/evals", json={"name": name, "input": f"Run job {name}.", "checker_type": "shell",
                                                "checker": checker, "split": split})
    assert r.status_code == 200, r.text
    return r.json()


def eval_runs(api, slug: str) -> list[dict]:
    return [r for r in api.get(f"/agents/{slug}/runs", params={"limit": 500}).json() if r["source"] == "eval"]


def test_flaky_case_passes_on_majority_of_three_and_results_are_cached(api, make_agent):
    agent = make_agent("flaky3", model="fake:flaky", system_prompt="Flaky worker, k=3.")
    slug = agent["slug"]
    assert agent["eval_repeats"] == 3
    flaky = add_case(api, slug, "flaky-job", FLAKY)
    steady = add_case(api, slug, "steady-job", "true")

    assert api.post(f"/agents/{slug}/evals/run", json={"version": 1}).status_code == 200
    res = wait_evals(api, slug, 1, 2)
    assert res["repeats"] == 3 and res["train"] == {"passed": 2, "total": 2}
    by = {x["case_id"]: x for x in res["results"]}
    assert (by[flaky["id"]]["passed"], by[flaky["id"]]["passes"], by[flaky["id"]]["runs"]) == (True, 2, 3)
    assert (by[steady["id"]]["passes"], by[steady["id"]]["runs"]) == (3, 3)
    assert "FLAKY.txt" in api.get(f"/runs/{by[flaky['id']]['run_id']}/diff").text  # representative passing run

    runs = eval_runs(api, slug)
    assert len(runs) == 6 and [r["eval_case_id"] for r in runs].count(flaky["id"]) == 3
    assert all(r["status"] == "succeeded" and r["version_n"] == 1 for r in runs)

    api.post(f"/agents/{slug}/evals/run", json={"version": 1})  # cached per version: nothing re-runs
    again = wait_evals(api, slug, 1, 2)
    assert len(eval_runs(api, slug)) == 6 and again["results"] == res["results"]


def test_even_k_tie_fails_and_representative_run_is_a_failing_one(api, make_agent):
    agent = make_agent("flaky2", model="fake:flaky", system_prompt="Flaky worker, k=2.")
    slug = agent["slug"]
    r = api.patch(f"/agents/{slug}", json={"eval_repeats": 2})
    assert r.status_code == 200 and r.json()["eval_repeats"] == 2
    assert api.get(f"/agents/{slug}").json()["eval_repeats"] == 2
    flaky = add_case(api, slug, "flaky-tie", FLAKY)

    api.post(f"/agents/{slug}/evals/run", json={})
    [row] = wait_evals(api, slug, 1, 1)["results"]
    assert (row["case_id"], row["passed"], row["passes"], row["runs"]) == (flaky["id"], False, 1, 2)
    assert "FLAKY.txt" not in api.get(f"/runs/{row['run_id']}/diff").text
    assert row["reason"].startswith("exit 1")
    assert len(eval_runs(api, slug)) == 2


def test_improvement_compares_majority_verdicts(api, make_agent):
    """The base passes the flaky case 2/3 (majority pass) and fails the marker case 0/3; the optimizer is shown
    the representative failing run, and the comparison rows carry pass counts for both sides."""
    agent = make_agent("flakyim", model="fake:flaky", system_prompt="Flaky worker, improve.")
    slug = agent["slug"]
    flaky = add_case(api, slug, "flaky-im", FLAKY)
    marker = add_case(api, slug, "marker-im", "test -f MARKER.txt")

    r = api.post(f"/agents/{slug}/improve", json={})
    assert r.status_code == 200, r.text
    im = wait_improvement(api, r.json()["id"])
    rows = {c["case_id"]: c for c in im["cases"]}
    fb = rows[flaky["id"]]["base"]
    assert (fb["passed"], fb["passes"], fb["runs"]) == (True, 2, 3)
    assert (rows[marker["id"]]["base"]["passes"], rows[marker["id"]]["base"]["runs"]) == (0, 3)
    # fake:flaky ignores lessons, so the candidate (new system prompt, fresh counter) also passes the flaky case
    # 2/3 and fails the marker case: equal score, not eligible
    fc = rows[flaky["id"]]["candidate"]
    assert (fc["passed"], fc["passes"], fc["runs"]) == (True, 2, 3)
    assert im["status"] == "rejected" and im["regressions"] == []
    assert im["base"]["train"] == im["candidate"]["train"] == {"passed": 1, "total": 2}
    [edit] = im["lessons_diff"]
    assert rows[marker["id"]]["base"]["run_id"] in edit["evidence_run_ids"]
    assert len(eval_runs(api, slug)) == 12  # 2 cases x k=3 x 2 versions
