"""Authoring endpoints: agent drafts from a description, and lesson pruning jobs (see CONTRACT.md).

Pure logic lives in forge.drafting and forge.pruning; this module wires it to the database and the eval runner.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from forge import db, drafting, pruning
from forge.server import evals
from forge.server.auth import authorize, require_local
from forge.server.views import improvement_view

log = logging.getLogger("forge.authoring")
router = APIRouter(prefix="/v1")

CONFIG_KEYS = ("system_prompt", "lessons", "model", "tools", "http_tools", "network", "seed_files")
PRUNE_RULE = ("Pruning rule: a candidate is eligible when no case regresses and it passes at least as many cases "
              "as base. The goal is a smaller prompt at equal quality.")

# ---------------------------------------------------------------- drafts


class DraftIn(BaseModel):
    description: str
    examples: list[str] = Field(default_factory=list)
    constraints: str | None = None


def _free_slug(slug: str) -> str:
    if not db.get_agent_by_slug(slug):
        return slug
    for i in range(2, 1000):
        cand = f"{slug[:58]}-{i}"
        if not db.get_agent_by_slug(cand):
            return cand
    return f"{slug[:50]}-{db.new_id('x')[2:]}"


@router.post("/agent-drafts", dependencies=[Depends(require_local)])
async def create_draft(body: DraftIn) -> dict:
    """Drafts an agent and starter eval cases with the grader model. Creates nothing."""
    try:
        draft = await drafting.draft_agent(body.description, evals.grader(), examples=body.examples[:10],
                                           constraints=body.constraints)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except drafting.DraftError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001  provider or network failure
        log.exception("draft failed")
        raise HTTPException(502, f"The drafting model call failed: {type(exc).__name__}: {exc}") from exc
    draft["slug"] = _free_slug(draft["slug"])
    return draft


# ---------------------------------------------------------------- prune


class PruneIn(BaseModel):
    max_ablations: int = Field(default=pruning.DEFAULT_MAX_ABLATIONS, ge=1, le=30)


def _config(v: dict) -> dict:
    return {k: v[k] for k in CONFIG_KEYS}


def _identical_versions(agent_id: str, config: dict, exclude: str | None = None) -> list[dict]:
    return [v for v in db.list_versions(agent_id) if v["id"] != exclude and _config(v) == config]


def _reuse_results(agent_id: str, version: dict, cases: list[dict]) -> int:
    """Copies cached eval results from versions with an identical configuration: the per-run verdicts
    (eval_run_results) and the majority summary with its passes/runs. Only cases with no runs of their own are
    copied, so run indexes never clash; run_eval then tops them up to the current k. Returns cases copied."""
    from forge import improve

    have = evals.results_map(version["id"])
    own = {r["case_id"] for r in db.list_eval_run_results(version["id"])}
    copied = 0
    for other in _identical_versions(agent_id, _config(version), exclude=version["id"]):
        theirs = evals.results_map(other["id"])
        for c in cases:
            if c["id"] in have or c["id"] in own:
                continue
            runs = db.list_eval_run_results(other["id"], c["id"])
            if not runs:
                continue
            for r in runs:
                db.add_eval_run_result(version["id"], c["id"], r["idx"], r["run_id"], bool(r["passed"]), r["reason"])
            own.add(c["id"])
            if c["id"] in theirs:  # their summary, recomputed over the copied runs
                n = theirs[c["id"]]["runs"]
                s = improve.summarize_runs(runs[:n])
                db.upsert_eval_result(version["id"], c["id"], s["run_id"], s["passed"], s["reason"], s["passes"],
                                      s["runs"])
                have[c["id"]] = s
            copied += 1
    return copied


def _ablation_version(agent: dict, base: dict, lessons: list[str]) -> dict:
    """An existing version identical to base minus one lesson, or a new unpromoted one."""
    config = {**_config(base), "lessons": lessons}
    same = _identical_versions(agent["id"], config)
    if same:
        return same[0]
    return db.create_version(agent["id"], **config, parent_id=base["id"], created_by="optimizer",
                             rationale="ablation")


def _run_ids(results: dict[str, dict], cases: list[dict]) -> list[str]:
    return [results[c["id"]]["run_id"] for c in cases if c["id"] in results and results[c["id"]].get("run_id")]


@router.post("/agents/{slug}/prune")
async def prune(slug: str, request: Request, body: PruneIn = Body(default_factory=PruneIn)) -> dict:
    agent = db.get_agent_by_slug(slug)
    if agent is None:
        raise HTTPException(404, f"agent {slug!r} not found")
    authorize(request, agent, allow_share=False)
    if any(i["status"] == "running" for i in db.list_improvements(agent["id"])):
        raise HTTPException(409, "an improvement is already running for this agent")
    base = db.get_version(agent["prod_version_id"])
    if base is None:
        raise HTTPException(400, "agent has no prod version")
    if len(base["lessons"]) < pruning.MIN_LESSONS:
        raise HTTPException(400, f"pruning needs at least {pruning.MIN_LESSONS} lessons in production "
                                 f"(v{base['n']} has {len(base['lessons'])})")
    if not any(c["split"] == "train" for c in db.list_eval_cases(agent["id"])):
        raise HTTPException(400, "pruning needs at least one train evaluation case")
    im = db.create_improvement(agent["id"], base["id"], kind="prune")
    ctx = contextvars.copy_context()  # eval runs and judge calls are attributed to this prune job (observability)
    ctx.run(evals.current_improvement.set, im["id"])
    t = asyncio.create_task(_prune(agent, base, im["id"], body.max_ablations), context=ctx)
    evals._improve_tasks[im["id"]] = t  # so evals.wait_improvement works for prune jobs too
    t.add_done_callback(lambda _t: evals._improve_tasks.pop(im["id"], None))
    return improvement_view(im)


async def _prune(agent: dict, base: dict, improvement_id: str, max_ablations: int) -> None:
    from forge import improve

    summary: dict[str, Any] = {"lessons_diff": [], "ablations": [], "error": None, "rule": PRUNE_RULE}
    try:
        await evals.run_eval(agent, base)
        cases = db.list_eval_cases(agent["id"])
        train = [c for c in cases if c["split"] == "train"]
        base_res = evals.results_map(base["id"])
        k = evals.eval_repeats(db.get_agent(agent["id"]) or agent)
        b_pass, total, complete = pruning.train_passes(base_res, train, k)
        if not complete:
            raise RuntimeError("the base version could not be evaluated on every train case")

        lessons = list(base["lessons"])
        targets = pruning.select_for_ablation(lessons, base.get("lesson_meta"), max_ablations)
        sem = asyncio.Semaphore(evals.EVAL_CONCURRENCY)  # one sandbox bound shared by all ablations

        async def ablate(lesson: str) -> dict:
            v = _ablation_version(agent, base, [x for x in lessons if x != lesson])
            _reuse_results(agent["id"], v, train)
            await evals.run_eval(agent, v, cases=train, sem=sem)  # majority of k, cached per version
            res = evals.results_map(v["id"])
            a_pass, _, ok = pruning.train_passes(res, train, k)
            verdict = pruning.classify(b_pass, a_pass, ok)
            return {"lesson": lesson, "version": v["n"], "verdict": verdict,
                    "base_train": {"passed": b_pass, "total": total},
                    "train": {"passed": a_pass, "total": total},
                    "evidence": pruning.kept_evidence(verdict, b_pass, a_pass, total),
                    "run_ids": _run_ids(res, train)}

        ablations = list(await asyncio.gather(*(ablate(x) for x in targets)))
        skipped = [{"lesson": x, "version": None, "verdict": "not_ablated", "evidence":
                    f"not ablated: over the cap of {max_ablations} lessons per job"} for x in lessons if x not in targets]
        summary["ablations"] = ablations + skipped
        unhelpful = [a for a in ablations if a["verdict"] == "unhelpful"]
        summary["lessons_diff"] = [{"op": "remove", "text": a["lesson"], "evidence_run_ids": a["run_ids"],
                                    "rationale": a["evidence"]} for a in unhelpful]
        _note_on_base(base, ablations, improvement_id)
        if not unhelpful:
            summary["error"] = "Every ablated lesson helped on train cases, so there is nothing to prune."
            db.update_improvement(improvement_id, status="rejected", summary=summary, ended_at=time.time())
            return

        removed = {a["lesson"] for a in unhelpful}
        kept = [x for x in lessons if x not in removed]
        cand = db.create_version(
            agent["id"], **{**_config(base), "lessons": kept}, parent_id=base["id"], created_by="optimizer",
            rationale=f"Pruning: removed {len(removed)} of {len(lessons)} lessons whose removal did not reduce "
                      f"train passes ({b_pass}/{total}).")
        db.set_lesson_meta(cand["id"], _candidate_meta(base, kept, ablations, improvement_id, cand["n"]))
        db.update_improvement(improvement_id, candidate_version_id=cand["id"], summary=summary)
        _reuse_results(agent["id"], cand, cases)
        await evals.run_eval(agent, cand)
        cand_res = evals.results_map(cand["id"])
        cmp = improve.compare(cases, base_res, cand_res)
        cmp.pop("eligible")
        summary.update(cmp)
        status = "eligible" if pruning.is_eligible_prune(cases, base_res, cand_res) else "rejected"
        db.update_improvement(improvement_id, status=status, summary=summary, ended_at=time.time())
    except Exception as exc:  # noqa: BLE001
        log.exception("prune %s failed", improvement_id)
        summary["error"] = f"{type(exc).__name__}: {exc}"
        db.update_improvement(improvement_id, status="failed", summary=summary, ended_at=time.time())


def _prune_note(a: dict, improvement_id: str) -> dict:
    return {"improvement_id": improvement_id, "verdict": a["verdict"], "evidence": a["evidence"], "at": time.time()}


def _note_on_base(base: dict, ablations: list[dict], improvement_id: str) -> None:
    """Records each ablated lesson's verdict on the base version's provenance ("kept after pruning")."""
    meta = dict(base.get("lesson_meta") or {})
    for a in ablations:
        meta[a["lesson"]] = {**(meta.get(a["lesson"]) or {}), "pruning": _prune_note(a, improvement_id)}
    db.set_lesson_meta(base["id"], meta)


def _candidate_meta(base: dict, kept: list[str], ablations: list[dict], improvement_id: str, n: int) -> dict:
    by_lesson = {a["lesson"]: a for a in ablations}
    old = base.get("lesson_meta") or {}
    meta: dict[str, dict] = {}
    for text in kept:
        entry = dict(old.get(text) or {})
        if text in by_lesson:
            entry["pruning"] = {**_prune_note(by_lesson[text], improvement_id), "version": n}
        if entry:
            meta[text] = entry
    return meta
