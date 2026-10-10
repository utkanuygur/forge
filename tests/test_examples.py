"""Example agents in demo/examples: deterministic generators, well formed definitions, judge plumbing.

The shell checkers themselves are verified in the real sandbox by demo/verify_examples.py (each fails on
the seed, fails on a plausible wrong answer where one exists, and passes on a reference solution).
"""

from __future__ import annotations

import asyncio
import filecmp
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from forge.improve import check_case
from forge.providers import ScenarioProvider
from forge.types import ExecResult, ModelResponse

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "demo" / "examples"
GENERATED = ["agent.json", "evals.json", "prompt_checks.json"]
SLUGS = sorted(p.parent.name for p in EXAMPLES.glob("*/agent.json"))
GENERATED_SEEDS = ["log-triage", "data-analyst"]  # seeds written by build.py; the others are hand written


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


load_examples = _module(REPO / "demo" / "load_examples.py", "load_examples")


def _build(slug: str, out: Path) -> None:
    subprocess.run([sys.executable, str(EXAMPLES / slug / "build.py"), "--out", str(out)], check=True,
                   capture_output=True)


def _same_tree(a: Path, b: Path) -> list[str]:
    cmp = filecmp.dircmp(a, b)
    diffs = [f"{a.name}/{n}" for n in cmp.left_only + cmp.right_only + cmp.diff_files + cmp.funny_files]
    for sub in cmp.common_dirs:
        diffs += _same_tree(a / sub, b / sub)
    return diffs


def test_expected_examples_exist():
    assert SLUGS == ["data-analyst", "docs-writer", "log-triage", "repo-maintainer"]


@pytest.mark.parametrize("slug", SLUGS)
def test_build_is_deterministic_and_matches_committed_files(slug, tmp_path):
    """The same bytes on every run, and the committed seed and JSON files are exactly what build.py writes."""
    one, two = tmp_path / "one", tmp_path / "two"
    _build(slug, one)
    _build(slug, two)
    for name in [n for n in GENERATED if (one / n).exists() or n != "evals.json"]:  # repo-maintainer has no own evals
        assert (one / name).read_bytes() == (two / name).read_bytes(), name
        assert (one / name).read_bytes() == (EXAMPLES / slug / name).read_bytes(), f"{slug}/{name} is stale: rebuild"
    if slug in GENERATED_SEEDS:
        assert not _same_tree(one / "seed", two / "seed")
        assert not _same_tree(one / "seed", EXAMPLES / slug / "seed"), "seed is stale: rebuild"


@pytest.mark.parametrize("slug", SLUGS)
def test_example_definition_is_well_formed(slug):
    ex = load_examples.resolve(EXAMPLES / slug)
    spec = ex["spec"]
    assert spec["slug"] == slug and spec["name"] and spec["system_prompt"].strip()
    assert spec["network"] is False and set(spec["tools"]) == {"bash", "read_file", "write_file", "edit_file"}
    prompts = spec["suggested_prompts"]
    assert 3 <= len(prompts) <= 12
    assert len({p["title"] for p in prompts}) == len(prompts)
    for p in prompts:
        assert set(p) == {"title", "prompt"} and 0 < len(p["title"]) <= 80 and 0 < len(p["prompt"]) <= 20_000
    cases = ex["cases"]
    names = [c["name"] for c in cases]
    assert len(set(names)) == len(names)
    assert {c["split"] for c in cases} == {"train", "holdout"}
    assert not {p["prompt"] for p in prompts} & {c["input"] for c in cases}, "suggested prompts must not be eval cases"
    for c in cases:
        assert set(load_examples.CASE_FIELDS) <= set(c) and c["checker"].strip()
    checks = json.loads((EXAMPLES / slug / "prompt_checks.json").read_text())
    assert set(checks) <= {p["title"] for p in prompts}
    seeds = load_examples.read_seed_files(ex["seed_dir"])
    assert seeds and all(len(v.encode()) < 1_000_000 for v in seeds.values())
    for name in ("answer.json", "results.json", "report.md"):
        assert name not in seeds, "outputs must not be pre seeded"


def test_example_splits():
    split = {slug: {c["name"]: c["split"] for c in load_examples.resolve(EXAMPLES / slug)["cases"]}
             for slug in SLUGS}
    assert sum(v == "holdout" for v in split["log-triage"].values()) == 1 and len(split["log-triage"]) == 4
    assert split["data-analyst"]["q3-refund-rate"] == "holdout"
    assert split["docs-writer"]["api-docs-serialize"] == split["docs-writer"]["release-notes-0-5"] == "holdout"


def test_repo_maintainer_reuses_the_reference_demo_unchanged():
    ex = load_examples.resolve(EXAMPLES / "repo-maintainer")
    base = json.loads((REPO / "demo" / "repo-maintainer.json").read_text())
    assert {k: ex["spec"][k] for k in base} == base
    assert ex["seed_dir"] == (REPO / "demo" / "acme-orders").resolve()
    assert ex["cases"] == json.loads((REPO / "demo" / "evals.json").read_text())
    assert len(ex["spec"]["suggested_prompts"]) == 4


def test_ground_truth_is_planted_where_documented():
    """The headline answers the docs promise (docs/test-cases.md)."""
    def truth(slug: str, case: str) -> dict:
        c = next(c for c in load_examples.resolve(EXAMPLES / slug)["cases"] if c["name"] == case)
        line = next(x for x in c["checker"].splitlines() if x.startswith("TRUTH = "))
        return json.loads(json.loads(line.split("json.loads(", 1)[1].rsplit(")", 1)[0]))

    deploy = truth("log-triage", "checkout-bad-deploy")
    assert (deploy["endpoint"], deploy["bad_version"], deploy["rollback_version"], deploy["exception"]) == \
        ("POST /api/v1/checkout", "2.14.0", "2.13.4", "KeyError")
    assert truth("log-triage", "orders-slow-queries")["application_name"] == "reporting-cron"
    assert [c["host"] for c in truth("log-triage", "expiring-certificates")] == \
        ["payments.acme.example", "grafana.internal.acme.example"]
    assert truth("log-triage", "catalog-scraper")["ip"] == "203.0.113.77"
    growth = truth("data-analyst", "q3-mom-growth")
    assert (growth["region"], growth["month"]) == ("Asia Pacific", "2026-08")
    assert len(truth("data-analyst", "duplicate-customers")["duplicate_groups"]) == 14
    assert truth("data-analyst", "q3-refund-rate")["highest"] == "Electronics"


# ---------------------------------------------------------------- judge cases with a fake grader


class DiffExecutor:
    """Answers the judge's `git diff` with a fixed diff, like a sandbox would."""

    def __init__(self, diff: str) -> None:
        self.diff = diff

    async def exec(self, cmd: str, timeout: int = 120) -> ExecResult:
        assert "git diff" in cmd
        return ExecResult(exit_code=0, stdout=self.diff, stderr="")


class RecordingGrader:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, system, messages, tools, temperature=None) -> ModelResponse:
        self.prompts.append(messages[-1].text)
        return ModelResponse(text='{"passed": true, "reason": "rubric met"}', tokens_in=1, tokens_out=1)


def _reference_diff(slug: str, case: str, tmp_path: Path) -> str:
    ws = tmp_path / "ws"
    shutil.copytree(EXAMPLES / slug / "seed", ws)
    before = {p.relative_to(ws).as_posix(): p.read_text() for p in ws.rglob("*") if p.is_file()}
    sol = EXAMPLES / slug / "solutions"
    if (sol / case).is_dir():
        shutil.copytree(sol / case, ws, dirs_exist_ok=True)
    else:
        subprocess.run([sys.executable, str(sol / f"{case}.py"), str(ws)], check=True, env={"PYTHONPATH": str(sol)})
    out = []
    for p in sorted(ws.rglob("*")):
        rel = p.relative_to(ws).as_posix()
        if p.is_file() and before.get(rel) != p.read_text():
            out.append(f"+++ b/{rel}\n" + "".join(f"+{line}\n" for line in p.read_text().splitlines()))
    return "\n".join(out)


JUDGE_CASES = [(slug, c) for slug in SLUGS for c in load_examples.resolve(EXAMPLES / slug)["cases"]
               if c["checker_type"] == "judge" and load_examples.resolve(EXAMPLES / slug)["own_evals"]]


def test_there_are_judge_cases():
    assert {(s, c["name"]) for s, c in JUDGE_CASES} == {
        ("data-analyst", "q3-growth-report-quality"), ("docs-writer", "readme-quickstart-quality"),
        ("docs-writer", "api-docs-cache"), ("docs-writer", "release-notes-0-5")}


@pytest.mark.parametrize("slug,case", JUDGE_CASES, ids=[c["name"] for _, c in JUDGE_CASES])
def test_judge_case_grades_the_reference_with_its_rubric(slug, case, tmp_path):
    """A judge case sends its full rubric, the task and the reference solution's files to the grader, and the
    fake grader scenario (fake:grader) passes it."""
    diff = _reference_diff(slug, case["name"], tmp_path)
    assert diff, "the reference solution must change files the rubric grades"
    grader = RecordingGrader()
    passed, reason = asyncio.run(check_case(case, DiffExecutor(diff), "Done.", grader))  # type: ignore[arg-type]
    assert passed and reason == "rubric met"
    prompt = grader.prompts[0]
    assert case["checker"] in prompt and case["input"][:200] in prompt and diff[:2000] in prompt
    rubric = case["checker"]
    assert "Pass only if every point holds" in rubric and rubric.count("\n") >= 4  # concrete, numbered criteria
    passed, _ = asyncio.run(check_case(case, DiffExecutor(diff), "Done.", ScenarioProvider("grader")))
    assert passed
