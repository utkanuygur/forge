"""Lesson pruning: retire lessons that never helped (pure decision logic; orchestration is forge.server.authoring).

Method (leave-one-out ablation on TRAIN cases):
  1. For each of the production version's lessons (at most `max_ablations`, oldest first), evaluate a temporary
     version without that lesson on the train cases.
  2. A lesson is UNHELPFUL when removing it does not reduce train passes (ties count as unhelpful: the lesson
     bought nothing measurable). It is HELPFUL when removal loses at least one train pass, and UNKNOWN (kept)
     when the ablation could not be fully evaluated.
  3. One candidate removes every unhelpful lesson at once and is evaluated on train AND held-out cases.

Pruning rule (`is_eligible_prune`): zero regressions and total passes >= base. Unlike the improvement rule
(strictly more passes), a tie is enough, because the goal is a smaller prompt at equal quality.
"""

from __future__ import annotations

from typing import Literal

DEFAULT_MAX_ABLATIONS = 8
MIN_LESSONS = 2

Verdict = Literal["unhelpful", "helpful", "unknown"]


def select_for_ablation(lessons: list[str], meta: dict | None = None,
                        max_ablations: int = DEFAULT_MAX_ABLATIONS) -> list[str]:
    """The lessons to ablate: oldest first, capped. Age is `created_in_version` from provenance when known;
    lessons are appended in order, so list position breaks ties and orders lessons without provenance."""
    meta = meta or {}

    def age(item: tuple[int, str]) -> tuple[int, int]:
        i, text = item
        v = (meta.get(text) or {}).get("created_in_version")
        return (v if isinstance(v, int) else 0, i)

    ordered = [t for _, t in sorted(enumerate(lessons), key=age)]
    return ordered[: max(0, max_ablations)]


def train_passes(results: dict[str, dict], train_cases: list[dict], k: int | None = None) -> tuple[int, int, bool]:
    """(passed, total, complete) over the train cases. `passed` counts majority-of-k verdicts (`results[case]
    ["passed"]` is the per-case summary, see forge.improve.summarize_runs). `complete` is False if any case has no
    result, or (given k) a summary that covers fewer than k runs."""
    passed, complete = 0, True
    for c in train_cases:
        r = results.get(c["id"])
        if r is None or (k is not None and (r.get("runs") or 1) < k):
            complete = False
        elif r.get("passed"):
            passed += 1
    return passed, len(train_cases), complete


def classify(base_passed: int, ablated_passed: int, complete: bool = True) -> Verdict:
    """Removing a lesson that does not reduce train passes marks it unhelpful (ties included)."""
    if not complete:
        return "unknown"
    return "unhelpful" if ablated_passed >= base_passed else "helpful"


def is_eligible_prune(cases: list[dict], base: dict[str, dict], cand: dict[str, dict]) -> bool:
    """Pruning rule: no case passed by base fails for the candidate, and candidate passes >= base passes."""
    from forge.improve import compare

    cmp = compare(cases, base, cand)
    base_total = sum(v["passed"] for v in cmp["base"].values())
    cand_total = sum(v["passed"] for v in cmp["candidate"].values())
    return not cmp["regressions"] and cand_total >= base_total


def removal_evidence(base_passed: int, ablated_passed: int, total: int) -> str:
    if ablated_passed == base_passed:
        return f"removing this lesson kept train at {ablated_passed}/{total}"
    return f"removing this lesson raised train from {base_passed}/{total} to {ablated_passed}/{total}"


def kept_evidence(verdict: Verdict, base_passed: int, ablated_passed: int, total: int) -> str:
    if verdict == "helpful":
        return f"removing this lesson dropped train from {base_passed}/{total} to {ablated_passed}/{total}"
    if verdict == "unknown":
        return "kept: its ablation could not be fully evaluated"
    return removal_evidence(base_passed, ablated_passed, total)
