"""Eval runner and improvement orchestration (pure logic lives in forge.improve)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from forge import db
from forge.server.runs import get_manager
from forge.types import AgentVersion, Provider

log = logging.getLogger("forge.evals")

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
EVAL_CONCURRENCY = 4
AUTO_IMPROVE_THRESHOLD = 3

_eval_tasks: dict[str, asyncio.Task] = {}  # version_id -> running eval
_improve_tasks: dict[str, asyncio.Task] = {}  # improvement_id -> task


def default_model() -> str:
    return os.environ.get("FORGE_DEFAULT_MODEL") or DEFAULT_MODEL


def grader() -> Provider:
    from forge.providers import make_provider

    return make_provider(os.environ.get("FORGE_GRADER_MODEL") or default_model())


def to_agent_version(v: dict) -> AgentVersion:
    return AgentVersion(**{k: v[k] for k in AgentVersion.model_fields if k in v})


# ---------------------------------------------------------------- evals


def results_map(version_id: str) -> dict[str, dict]:
    return {r["case_id"]: {"passed": r["passed"], "reason": r["reason"], "run_id": r["run_id"]}
            for r in db.list_eval_results(version_id)}


def eval_summary(agent: dict, version: dict) -> dict:
    cases = db.list_eval_cases(agent["id"])
    res = results_map(version["id"])
    splits: dict[str, dict[str, int]] = {"train": {"passed": 0, "total": 0}, "holdout": {"passed": 0, "total": 0}}
    for c in cases:
        b = splits.setdefault(c["split"], {"passed": 0, "total": 0})
        b["total"] += 1
        b["passed"] += int(bool(res.get(c["id"], {}).get("passed")))
    return {
        "version": version["n"], **splits,
        "results": [{"case_id": cid, **r} for cid, r in res.items()],
        "running": version["id"] in _eval_tasks,
    }


async def _eval_case(agent: dict, version: dict, case: dict, sem: asyncio.Semaphore, judge: list) -> None:
    from forge import improve

    async def after(executor: Any, result: Any) -> tuple[bool, str]:
        if not judge:
            judge.append(grader())
        return await improve.check_case(case, executor, result.output, judge[0])

    async with sem:
        mgr = get_manager()
        run_id = await mgr.start_run(agent, version, case["input"], "eval", eval_case=case, after=after,
                                     temperature=0)
        run = await mgr.wait(run_id)
        checked = mgr.after_results.pop(run_id, None)
        if checked is None:  # infrastructure failure before the check ran: do not cache, retry next time
            log.warning("eval run %s for case %s errored: %s", run_id, case["id"], (run or {}).get("error"))
            return
        db.upsert_eval_result(version["id"], case["id"], run_id, bool(checked[0]), str(checked[1]))


async def run_eval(agent: dict, version: dict) -> dict:
    """Evaluates `version` on every case lacking a cached result. Concurrent calls for one version share work."""
    task = _eval_tasks.get(version["id"])
    if task is None:
        task = asyncio.create_task(_run_eval(agent, version))
        _eval_tasks[version["id"]] = task
        task.add_done_callback(lambda _t: _eval_tasks.pop(version["id"], None))
    await asyncio.shield(task)
    return eval_summary(agent, version)


async def _run_eval(agent: dict, version: dict) -> None:
    cached = results_map(version["id"])
    todo = [c for c in db.list_eval_cases(agent["id"]) if c["id"] not in cached]
    sem, judge = asyncio.Semaphore(EVAL_CONCURRENCY), []
    await asyncio.gather(*(_eval_case(agent, version, c, sem, judge) for c in todo))


def start_eval(agent: dict, version: dict) -> None:
    """Fire-and-forget eval for the HTTP endpoint."""
    if version["id"] not in _eval_tasks:
        t = asyncio.create_task(run_eval(agent, version))
        t.add_done_callback(lambda t: t.cancelled() or (t.exception() and log.error("eval failed: %r", t.exception())))


# ---------------------------------------------------------------- improvement


def _last_improvement_time(agent_id: str, exclude: str | None = None) -> float:
    ims = [i for i in db.list_improvements(agent_id) if i["id"] != exclude]
    return ims[0]["created_at"] if ims else 0.0


def _events(run_id: str) -> list[dict]:
    return [{k: e[k] for k in ("run_id", "seq", "ts", "type", "payload")} for e in db.list_events(run_id)]


def start_improve(agent: dict) -> dict:
    """Creates the improvement row and launches the cycle in the background."""
    base = db.get_version(agent["prod_version_id"])
    if base is None:
        raise ValueError("agent has no prod version")
    im = db.create_improvement(agent["id"], base["id"])
    t = asyncio.create_task(_improve(agent, base, im["id"]))
    _improve_tasks[im["id"]] = t
    t.add_done_callback(lambda _t: _improve_tasks.pop(im["id"], None))
    return im


async def wait_improvement(improvement_id: str) -> None:
    t = _improve_tasks.get(improvement_id)
    if t is not None:
        await asyncio.shield(t)


def _rationale(edits: list[dict]) -> str:
    parts = []
    for e in edits:
        parts.append(f"{e['op']}: {e['text'][:120]}" + (f" ({e['rationale'][:120]})" if e.get("rationale") else ""))
    return "Optimizer lesson edits. " + "; ".join(parts)


async def _improve(agent: dict, base: dict, improvement_id: str) -> None:
    from forge import improve

    summary: dict[str, Any] = {"lessons_diff": [], "error": None}
    try:
        since = _last_improvement_time(agent["id"], exclude=improvement_id)
        await run_eval(agent, base)
        cases = db.list_eval_cases(agent["id"])
        train = [c for c in cases if c["split"] == "train"]
        base_res = results_map(base["id"])
        failures = []
        for c in train:
            r = base_res.get(c["id"])
            if r and not r["passed"]:
                run = db.get_run(r["run_id"]) or {}
                failures.append({"run_id": r["run_id"], "input": c["input"], "output": run.get("output", ""),
                                 "reason": r["reason"], "events": _events(r["run_id"])})
        feedback = [{"run_id": f["run_id"], "input": f["run_input"], "output": f["run_output"],
                     "rating": f["rating"], "comment": f["comment"], "events": _events(f["run_id"])}
                    for f in db.negative_feedback_since(agent["id"], since)]
        edits = await improve.propose_lessons(to_agent_version(base), failures, feedback, grader(), train)
        diff = [e.model_dump() if hasattr(e, "model_dump") else dict(e) for e in edits]
        summary["lessons_diff"] = diff
        if not edits:
            summary["error"] = "optimizer proposed no lesson edits"
            db.update_improvement(improvement_id, status="rejected", summary=summary, ended_at=time.time())
            return
        lessons = improve.apply_lesson_edits(list(base["lessons"]), edits)
        cand = db.create_version(
            agent["id"], **{k: base[k] for k in ("system_prompt", "model", "tools", "http_tools", "network",
                                                 "seed_files", "mcp_servers", "callable_agents")},
            lessons=lessons, parent_id=base["id"], created_by="optimizer", rationale=_rationale(diff))
        db.update_improvement(improvement_id, candidate_version_id=cand["id"], summary=summary)
        await run_eval(agent, cand)
        cmp = improve.compare(cases, base_res, results_map(cand["id"]))
        eligible = bool(cmp.pop("eligible"))
        summary.update(cmp)
        status = "eligible" if eligible else "rejected"
        fresh = db.get_agent(agent["id"]) or agent
        if eligible and fresh["auto_promote"]:
            db.update_agent(agent["id"], prod_version_id=cand["id"])
            status = "promoted"
        db.update_improvement(improvement_id, status=status, summary=summary, ended_at=time.time())
    except Exception as exc:  # noqa: BLE001
        log.exception("improvement %s failed", improvement_id)
        summary["error"] = f"{type(exc).__name__}: {exc}"
        db.update_improvement(improvement_id, status="failed", summary=summary, ended_at=time.time())


def maybe_auto_improve(agent: dict) -> dict | None:
    """Starts an improvement for auto_promote agents once enough new negative feedback has arrived."""
    if not agent["auto_promote"] or not agent["prod_version_id"]:
        return None
    ims = db.list_improvements(agent["id"])
    if any(i["status"] == "running" for i in ims):
        return None
    since = ims[0]["created_at"] if ims else 0.0
    if len(db.negative_feedback_since(agent["id"], since)) < AUTO_IMPROVE_THRESHOLD:
        return None
    return start_improve(agent)
