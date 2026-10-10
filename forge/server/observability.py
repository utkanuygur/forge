"""Observability routes: metrics, run search, run timelines, health check and Prometheus metrics.

SQL here only fetches rows (aggregating where it is simple); the arithmetic lives in `forge.metrics`.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from collections import defaultdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from forge import db, metrics, pricing
from forge.server.auth import authorize, is_local, require_local

api = APIRouter(prefix="/v1")
ops = APIRouter()

SortKey = Literal["created_at", "duration", "cost"]

# ---------------------------------------------------------------- shared helpers


def _rows(sql: str, args: list | tuple = ()) -> list[dict]:
    with db.connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def _agent_or_404(slug: str) -> dict:
    a = db.get_agent_by_slug(slug)
    if a is None:
        raise HTTPException(404, f"agent {slug!r} not found")
    return a


def price_view(model: str | None) -> dict:
    return {"model": model, **pricing.price_for(model).to_dict()}


def cost_fields(model: str | None, tokens_in: int, tokens_out: int) -> dict:
    """Additive Run fields: `cost_usd` (null for unknown prices) and `cost_note`."""
    return {"cost_usd": pricing.cost(model, tokens_in or 0, tokens_out or 0),
            "cost_note": pricing.price_for(model).note}


def _versions(agent_id: str) -> dict[str, dict]:
    return {v["id"]: v for v in _rows(
        "SELECT id, n, model, created_by, created_at, rationale FROM agent_versions WHERE agent_id = ?", (agent_id,))}


def _annotate(runs: list[dict], versions: dict[str, dict]) -> list[dict]:
    """Adds version_n, model and cost_usd (None for unknown prices) to run rows."""
    for r in runs:
        v = versions.get(r["version_id"]) or {}
        r["version_n"], r["model"] = v.get("n"), v.get("model")
        r["cost_usd"] = pricing.cost(r["model"], r["tokens_in"], r["tokens_out"])
    return runs


RUN_COLS = ("r.id, r.agent_id, r.version_id, r.source, r.status, r.error, r.turns, r.tokens_in, r.tokens_out, "
            "r.created_at, r.started_at, r.ended_at, r.eval_case_id, substr(r.input, 1, 160) AS input")


def _feedback_by_run(where: str, args: list) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for f in _rows(f"SELECT f.run_id, f.rating, f.comment, f.created_at FROM feedback f JOIN runs r ON r.id = f.run_id "
                   f"WHERE {where} ORDER BY f.created_at", args):
        out[f["run_id"]].append(f)
    return out


def _tool_errors_by_run(where: str, args: list) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    sql = ("SELECT e.run_id, json_extract(e.payload, '$.name') AS name, "
           "substr(json_extract(e.payload, '$.output'), 1, 600) AS output FROM events e JOIN runs r ON r.id = e.run_id "
           f"WHERE {where} AND e.type = 'tool_result' AND json_extract(e.payload, '$.is_error') = 1 "
           "ORDER BY e.run_id, e.seq")
    for row in _rows(sql, args):
        out[row["run_id"]].append(row)
    return out


def _tool_calls(where: str, args: list, versions: dict[str, dict]) -> list[dict]:
    sql = ("SELECT e.run_id, e.seq, e.ts, e.type, r.version_id, json_extract(e.payload, '$.id') AS call_id, "
           "json_extract(e.payload, '$.name') AS name, json_extract(e.payload, '$.is_error') AS is_error, "
           "json_extract(e.payload, '$.duration_ms') AS duration_ms, "
           "coalesce(json_extract(e.payload, '$.args.cmd'), json_extract(e.payload, '$.args.path')) AS arg "
           "FROM events e JOIN runs r ON r.id = e.run_id "
           f"WHERE {where} AND e.type IN ('tool_call', 'tool_result')")
    evs = []
    for row in _rows(sql, args):
        row["version_n"] = (versions.get(row.pop("version_id")) or {}).get("n")
        row["args"] = {"cmd": row.pop("arg")} if row.get("arg") else {}
        evs.append(row)
    return metrics.tool_calls_from_events(evs)


def _window(days: float, now: float | None = None) -> tuple[float, float, float]:
    until = now if now is not None else time.time()
    since = until - days * metrics.DAY
    return since, until, since - days * metrics.DAY


def _classify(runs: list[dict], feedback: dict[str, list[dict]], tool_errors: dict[str, list[dict]],
              eval_results: dict[str, dict] | None = None) -> list[tuple[dict, list[dict]]]:
    out = []
    for r in sorted(runs, key=lambda r: -r["created_at"]):
        found = metrics.classify_run(r, tool_errors=tool_errors.get(r["id"]), feedback=feedback.get(r["id"]),
                                     eval_result=(eval_results or {}).get(r["id"]))
        out.append((r, found))
    return out


# ---------------------------------------------------------------- run search


def search_runs(*, agent_id: str | None = None, status: str | None = None, version: str | None = None,
                source: str | None = None, rating: str | None = None, has_tool_errors: bool | None = None,
                q: str | None = None, since: float | None = None, until: float | None = None,
                sort: SortKey = "created_at", order: str = "desc", limit: int = 50,
                offset: int = 0) -> tuple[list[dict], int]:
    """Filtered run rows (full `runs` columns plus agent_slug, version_n and model) and the total match count."""
    where, args = ["1 = 1"], []

    def any_of(col: str, raw: str | None, cast: Any = str) -> None:
        vals = [x.strip() for x in (raw or "").split(",") if x.strip()]
        if vals:
            try:
                parsed = [cast(v) for v in vals]
            except ValueError as exc:
                raise HTTPException(400, f"invalid filter value for {col}") from exc
            where.append(f"{col} IN ({', '.join('?' for _ in parsed)})")
            args.extend(parsed)

    if agent_id:
        where.append("r.agent_id = ?")
        args.append(agent_id)
    any_of("r.status", status)
    any_of("v.n", version, int)
    any_of("r.source", source)
    if rating == "up":
        where.append("EXISTS (SELECT 1 FROM feedback f WHERE f.run_id = r.id AND f.rating > 0)")
    elif rating == "down":
        where.append("EXISTS (SELECT 1 FROM feedback f WHERE f.run_id = r.id AND f.rating < 0)")
    elif rating == "none":
        where.append("NOT EXISTS (SELECT 1 FROM feedback f WHERE f.run_id = r.id)")
    elif rating:
        raise HTTPException(400, "rating must be up, down or none")
    if has_tool_errors is not None:
        exists = ("EXISTS (SELECT 1 FROM events e WHERE e.run_id = r.id AND e.type = 'tool_result' "
                  "AND json_extract(e.payload, '$.is_error') = 1)")
        where.append(exists if has_tool_errors else f"NOT {exists}")
    if q and q.strip():
        pat = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(r.input LIKE ? ESCAPE '\\' OR r.output LIKE ? ESCAPE '\\')")
        args += [pat, pat]
    if since is not None:
        where.append("r.created_at >= ?")
        args.append(since)
    if until is not None:
        where.append("r.created_at <= ?")
        args.append(until)
    base = ("FROM runs r JOIN agents a ON a.id = r.agent_id JOIN agent_versions v ON v.id = r.version_id "
            f"WHERE {' AND '.join(where)}")
    direction = "ASC" if order == "asc" else "DESC"
    sort_args: list = []
    if sort == "duration":
        key = "(r.ended_at - r.started_at)"
    elif sort == "cost":
        models = [m["model"] for m in _rows("SELECT DISTINCT model FROM agent_versions")]
        cin, cout = [], []
        for m in models:
            p = pricing.price_for(m)
            cin += [m, p.input or 0.0]
            cout += [m, p.output or 0.0]
        case = lambda: "CASE v.model " + " ".join("WHEN ? THEN ?" for _ in models) + " ELSE 0 END"  # noqa: E731
        key = f"(r.tokens_in * {case()} + r.tokens_out * {case()})" if models else "0"
        sort_args = cin + cout if models else []
    else:
        key = "r.created_at"
    with db.connect() as c:
        total = c.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
        rows = c.execute(f"SELECT r.*, a.slug AS agent_slug, v.n AS version_n, v.model AS model {base} "
                         f"ORDER BY {key} {direction} NULLS LAST, r.created_at DESC LIMIT ? OFFSET ?",
                         [*args, *sort_args, limit, offset]).fetchall()
    return [db._decode(r) for r in rows], total  # type: ignore[misc]


def run_list_view(rows: list[dict]) -> list[dict]:
    from forge.server.views import run_view

    out = []
    for r in rows:
        v = run_view(r, version_n=r["version_n"], model=r["model"])
        v["agent_slug"] = r["agent_slug"]
        out.append(v)
    return out


def run_filters(
    status: str | None = Query(None, description="Comma separated statuses"),
    version: str | None = Query(None, description="Comma separated version numbers"),
    source: str | None = Query(None, description="Comma separated sources"),
    rating: Literal["up", "down", "none"] | None = None,
    has_tool_errors: bool | None = None,
    q: str | None = Query(None, max_length=500),
    since: float | None = None,
    until: float | None = None,
    sort: SortKey = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return {"status": status, "version": version, "source": source, "rating": rating,
            "has_tool_errors": has_tool_errors, "q": q, "since": since, "until": until, "sort": sort,
            "order": order, "limit": limit, "offset": offset}


@api.get("/runs", dependencies=[Depends(require_local)])
async def list_all_runs(response: Response, filters: dict = Depends(run_filters),
                        agent: str | None = Query(None, description="Agent slug")) -> list[dict]:
    agent_id = _agent_or_404(agent)["id"] if agent else None
    rows, total = await asyncio.to_thread(lambda: search_runs(agent_id=agent_id, **filters))
    response.headers["X-Total-Count"] = str(total)
    return run_list_view(rows)


# ---------------------------------------------------------------- timeline


@api.get("/runs/{run_id}/timeline")
async def run_timeline(run_id: str, request: Request) -> dict:
    r = db.get_run(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    agent = db.get_agent(r["agent_id"])
    authorize(request, agent, allow_share=False)  # type: ignore[arg-type]
    v = db.get_version(r["version_id"]) or {}
    tl = metrics.build_timeline(r, db.list_events(run_id))
    tl.update(tokens_in=r["tokens_in"], tokens_out=r["tokens_out"], price=price_view(v.get("model")),
              **cost_fields(v.get("model"), r["tokens_in"], r["tokens_out"]))
    return tl


# ---------------------------------------------------------------- agent metrics


def _eval_scores(agent_id: str) -> dict[str, dict]:
    totals = {row["split"]: row["n"] for row in _rows(
        "SELECT split, COUNT(*) AS n FROM eval_cases WHERE agent_id = ? GROUP BY split", (agent_id,))}
    out: dict[str, dict] = {}
    for row in _rows("SELECT er.version_id, ec.split, SUM(er.passed) AS passed, COUNT(*) AS evaluated "
                     "FROM eval_results er JOIN eval_cases ec ON ec.id = er.case_id WHERE ec.agent_id = ? "
                     "GROUP BY er.version_id, ec.split", (agent_id,)):
        d = out.setdefault(row["version_id"], {s: {"passed": 0, "total": totals.get(s, 0), "evaluated": 0}
                                               for s in ("train", "holdout")})
        d[row["split"]] = {"passed": row["passed"] or 0, "total": totals.get(row["split"], 0),
                           "evaluated": row["evaluated"]}
    for d in out.values():
        for s in ("train", "holdout"):
            d[s]["pass_rate"] = metrics.ratio(d[s]["passed"], d[s]["total"])
    return out


def _promotions(agent: dict, versions: dict[str, dict]) -> list[dict]:
    rows = db.list_promotions(agent["id"])
    if not rows and agent["prod_version_id"]:  # agents created before promotions were recorded
        rows = [{"version_id": agent["prod_version_id"], "from_version_id": None, "source": "create",
                 "created_at": agent["created_at"]}]
    n = lambda vid: (versions.get(vid) or {}).get("n") if vid else None  # noqa: E731
    return [{"version": n(p["version_id"]), "from_version": n(p["from_version_id"]), "source": p["source"],
             "at": p["created_at"]} for p in rows]


def _improvement_costs(agent_id: str, since: float, versions: dict[str, dict]) -> list[dict]:
    grader_rows = _rows("SELECT improvement_id, kind, model, tokens_in, tokens_out FROM grader_calls "
                        "WHERE agent_id = ? AND improvement_id IS NOT NULL", (agent_id,))
    grader: dict[str, list[dict]] = defaultdict(list)
    for g in grader_rows:
        grader[g["improvement_id"]].append(g)
    runs: dict[str, list[dict]] = defaultdict(list)
    for r in _rows("SELECT ir.improvement_id, r.version_id, r.tokens_in, r.tokens_out FROM improvement_runs ir "
                   "JOIN runs r ON r.id = ir.run_id WHERE r.agent_id = ?", (agent_id,)):
        runs[r["improvement_id"]].append(r)
    out = []
    for im in _rows("SELECT id, status, base_version_id, candidate_version_id, created_at, ended_at FROM improvements "
                    "WHERE agent_id = ? AND created_at >= ? ORDER BY created_at DESC", (agent_id, since)):
        eval_cost, unknown, tin, tout = 0.0, False, 0, 0
        for r in runs.get(im["id"], []):
            c = pricing.cost((versions.get(r["version_id"]) or {}).get("model"), r["tokens_in"], r["tokens_out"])
            unknown |= c is None
            eval_cost += c or 0.0
            tin, tout = tin + r["tokens_in"], tout + r["tokens_out"]
        judge_cost = opt_cost = 0.0
        g_in = g_out = 0
        for g in grader.get(im["id"], []):
            c = pricing.cost(g["model"], g["tokens_in"], g["tokens_out"])
            unknown |= c is None
            if g["kind"] == "optimizer":
                opt_cost += c or 0.0
            else:
                judge_cost += c or 0.0
            g_in, g_out = g_in + g["tokens_in"], g_out + g["tokens_out"]
        n = lambda vid: (versions.get(vid) or {}).get("n") if vid else None  # noqa: E731
        out.append({
            "id": im["id"], "status": im["status"], "base_version": n(im["base_version_id"]),
            "candidate_version": n(im["candidate_version_id"]), "created_at": im["created_at"],
            "ended_at": im["ended_at"], "eval_runs": len(runs.get(im["id"], [])), "eval_cost_usd": eval_cost,
            "judge_cost_usd": judge_cost, "optimizer_cost_usd": opt_cost,
            "grader_calls": len(grader.get(im["id"], [])), "grader_tokens_in": g_in, "grader_tokens_out": g_out,
            "tokens_in": tin + g_in, "tokens_out": tout + g_out,
            "total_cost_usd": eval_cost + judge_cost + opt_cost, "cost_complete": not unknown,
        })
    return out


VERSION_DELTA_KEYS = ("success_rate", "p50_ms", "p95_ms", "avg_turns", "thumbs_up_rate", "cost_per_run")


def agent_metrics(agent: dict, days: float, include_eval: bool, tz_offset_min: int = 0,
                  now: float | None = None) -> dict:
    since, until, prev_since = _window(days, now)
    tz = tz_offset_min * 60
    versions = _versions(agent["id"])
    all_runs = _annotate(_rows(f"SELECT {RUN_COLS} FROM runs r WHERE r.agent_id = ?", (agent["id"],)), versions)
    feedback = _feedback_by_run("r.agent_id = ?", [agent["id"]])
    scoped = [r for r in all_runs if metrics.include_source(r, include_eval)]
    cur = [r for r in scoped if since <= r["created_at"] <= until]
    prev = [r for r in scoped if prev_since <= r["created_at"] < since]
    fb_of = lambda rs: [f for r in rs for f in feedback.get(r["id"], [])]  # noqa: E731
    summary, previous = metrics.health(cur, fb_of(cur)), metrics.health(prev, fb_of(prev))

    # versions: lifetime production metrics next to eval pass rates
    evals = _eval_scores(agent["id"])
    promos = _promotions(agent, versions)
    prod_n = (versions.get(agent["prod_version_id"]) or {}).get("n")
    prev_prod_n = next((p["from_version"] for p in reversed(promos) if p["version"] == prod_n), None)
    by_version: dict[str, list[dict]] = defaultdict(list)
    for r in scoped:
        by_version[r["version_id"]].append(r)
    period_counts: dict[str, int] = defaultdict(int)
    for r in cur:
        period_counts[r["version_id"]] += 1
    version_rows = []
    for vid, v in sorted(versions.items(), key=lambda kv: -kv[1]["n"]):
        h = metrics.health(by_version.get(vid, []), fb_of(by_version.get(vid, [])))
        version_rows.append({
            "version": v["n"], "model": v["model"], "created_by": v["created_by"], "created_at": v["created_at"],
            "is_prod": v["n"] == prod_n, "is_previous_prod": v["n"] == prev_prod_n and prev_prod_n != prod_n,
            "promoted_at": max((p["at"] for p in promos if p["version"] == v["n"]), default=None),
            "price": price_view(v["model"]), "period_runs": period_counts.get(vid, 0),
            "health": h, "eval": evals.get(vid),
        })
    prod_row = next((x for x in version_rows if x["is_prod"]), None)
    prev_row = next((x for x in version_rows if x["version"] == prev_prod_n), None)
    if prod_row and prev_row and prev_row is not prod_row:
        d = {k: metrics.delta(prod_row["health"][k], prev_row["health"][k]) for k in VERSION_DELTA_KEYS}
        for split in ("train", "holdout"):
            a = ((prod_row["eval"] or {}).get(split) or {}).get("pass_rate")
            b = ((prev_row["eval"] or {}).get(split) or {}).get("pass_rate")
            d[f"{split}_pass_rate"] = metrics.delta(a, b)
        prod_row["delta_vs_previous_prod"] = d

    # cost (period)
    per_version = []
    for vid, v in sorted(versions.items(), key=lambda kv: -kv[1]["n"]):
        rs = [r for r in cur if r["version_id"] == vid]
        if not rs:
            continue
        known = [r["cost_usd"] for r in rs if r["cost_usd"] is not None]
        per_version.append({"version": v["n"], "model": v["model"], "price": price_view(v["model"]), "runs": len(rs),
                            "tokens_in": sum(r["tokens_in"] for r in rs), "tokens_out": sum(r["tokens_out"] for r in rs),
                            "cost_usd": sum(known) if known or not rs else None,
                            "cost_per_run": metrics.ratio(sum(known), len(known)) if known else None})

    # failures (period). Eval check failures are always included: they are a category of their own.
    period_where, period_args = "r.agent_id = ? AND r.created_at >= ? AND r.created_at <= ?", [agent["id"], since, until]
    tool_errors = _tool_errors_by_run(period_where, period_args)
    eval_res = {e["run_id"]: e for e in _rows(
        "SELECT er.run_id, er.passed, er.reason FROM eval_results er JOIN runs r ON r.id = er.run_id "
        f"WHERE {period_where}", period_args)}
    period_all = [r for r in all_runs if since <= r["created_at"] <= until]
    classify_set = [r for r in period_all if metrics.include_source(r, include_eval) or
                    (r["id"] in eval_res and not eval_res[r["id"]]["passed"])]
    classified = []
    for r, found in _classify(classify_set, feedback, tool_errors, eval_res):
        if not metrics.include_source(r, include_eval):
            found = [f for f in found if f["category"] == "eval_failed"]
        classified.append((r, found))
    failures = {"all": metrics.failure_breakdown(classified), "versions": []}
    for v in sorted(versions.values(), key=lambda v: -v["n"]):
        items = [(r, f) for r, f in classified if r["version_n"] == v["n"]]
        if any(f for _, f in items):
            failures["versions"].append({"version": v["n"], **metrics.failure_breakdown(items)})

    # tools (period, scoped)
    calls = _tool_calls(period_where, period_args, versions)
    scoped_ids = {r["id"] for r in cur}
    calls = [c for c in calls if c["run_id"] in scoped_ids]
    tools = metrics.tool_stats(calls)
    tools["versions"] = [{"version": n, "tools": metrics.tool_stats([c for c in calls if c["version_n"] == n])["tools"]}
                         for n in sorted({c["version_n"] for c in calls if c["version_n"] is not None}, reverse=True)]

    slowest = sorted((r for r in cur if metrics.run_duration_ms(r) is not None),
                     key=lambda r: -(metrics.run_duration_ms(r) or 0))[:10]
    return {
        "agent": agent["slug"], "days": days, "include_eval": include_eval, "since": since, "until": until,
        "bucket": "hour" if metrics.bucket_size(days) == metrics.HOUR else "day",
        "prod_version": prod_n, "previous_prod_version": prev_prod_n,
        "summary": summary, "previous": previous, "kpis": metrics.kpis(summary, previous),
        "series": metrics.series(cur, since, until, metrics.bucket_size(days), tz),
        "promotions": promos,
        "versions": version_rows,
        "cost": {"total_usd": summary["cost_usd"], "unpriced_runs": summary["unpriced_runs"],
                 "versions": per_version, "improvements": _improvement_costs(agent["id"], since, versions)},
        "failures": failures,
        "tools": tools,
        "slowest_runs": [{"id": r["id"], "version": r["version_n"], "status": r["status"], "source": r["source"],
                          "duration_ms": metrics.run_duration_ms(r), "input": r["input"], "cost_usd": r["cost_usd"],
                          "created_at": r["created_at"]} for r in slowest],
    }


@api.get("/agents/{slug}/metrics")
async def get_agent_metrics(slug: str, request: Request, days: float = Query(14, gt=0, le=365),
                            include_eval: bool = False, tz_offset_min: int = Query(0, ge=-840, le=840)) -> dict:
    agent = _agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    return await asyncio.to_thread(agent_metrics, agent, days, include_eval, tz_offset_min)


# ---------------------------------------------------------------- overview


def overview(days: float, include_eval: bool = False, tz_offset_min: int = 0, now: float | None = None) -> dict:
    since, until, prev_since = _window(days, now)
    agents = {a["id"]: a for a in db.list_agents()}
    versions: dict[str, dict] = {}
    for a in agents.values():
        versions.update(_versions(a["id"]))
    eval_clause = "" if include_eval else " AND r.source != 'eval'"
    rows = _annotate(_rows(f"SELECT {RUN_COLS} FROM runs r WHERE r.created_at >= ?{eval_clause}", (prev_since,)),
                     versions)
    where, args = f"r.created_at >= ?{eval_clause}", [prev_since]
    feedback = _feedback_by_run(where, args)
    cur = [r for r in rows if since <= r["created_at"] <= until]
    prev = [r for r in rows if r["created_at"] < since]
    fb_of = lambda rs: [f for r in rs for f in feedback.get(r["id"], [])]  # noqa: E731
    summary, previous = metrics.health(cur, fb_of(cur)), metrics.health(prev, fb_of(prev))
    size, tz = metrics.bucket_size(days), tz_offset_min * 60
    last_run = {row["agent_id"]: row["t"] for row in _rows(
        f"SELECT r.agent_id, MAX(r.created_at) AS t FROM runs r WHERE 1 = 1{eval_clause} GROUP BY r.agent_id")}
    tool_errors = _tool_errors_by_run(f"r.created_at >= ?{eval_clause}", [since])
    agent_rows, reasons = [], []
    for aid, a in agents.items():
        rs = [r for r in cur if r["agent_id"] == aid]
        h = metrics.health(rs, fb_of(rs))
        br = metrics.failure_breakdown(_classify(rs, feedback, tool_errors))
        for reason in br["reasons"]:
            reasons.append({**reason, "agent": a["slug"], "agent_name": a["name"]})
        agent_rows.append({
            "slug": a["slug"], "name": a["name"], "prod_version": (versions.get(a["prod_version_id"]) or {}).get("n"),
            "health": h, "failing_runs": h["failed"] + h["timeout"], "flagged_runs": br["runs"],
            "last_run_at": last_run.get(aid),
            "spark": [b["runs"] for b in metrics.series(rs, since, until, size, tz)],
        })
    agent_rows.sort(key=lambda x: (-x["health"]["runs"], x["slug"]))
    reasons.sort(key=lambda x: (-x["runs"], -x["count"], x["agent"]))
    failing = sorted((x for x in agent_rows if x["flagged_runs"]), key=lambda x: (-x["flagged_runs"], x["slug"]))
    return {
        "days": days, "include_eval": include_eval, "since": since, "until": until,
        "bucket": "hour" if size == metrics.HOUR else "day",
        "summary": summary, "previous": previous, "kpis": metrics.kpis(summary, previous),
        "series": metrics.series(cur, since, until, size, tz),
        "agents": agent_rows,
        "top_failure_reasons": reasons[:10],
        "top_failing_agents": [{"slug": x["slug"], "name": x["name"], "flagged_runs": x["flagged_runs"],
                                "failing_runs": x["failing_runs"]} for x in failing[:5]],
    }


@api.get("/metrics/overview", dependencies=[Depends(require_local)])
async def get_overview(days: float = Query(14, gt=0, le=365), include_eval: bool = False,
                       tz_offset_min: int = Query(0, ge=-840, le=840)) -> dict:
    return await asyncio.to_thread(overview, days, include_eval, tz_offset_min)


# ---------------------------------------------------------------- ops: health and Prometheus

_docker_cache: dict[str, Any] = {"at": 0.0, "value": None}


def check_docker() -> dict:
    """Docker daemon reachable and sandbox image present. Cached for 10 seconds."""
    if time.monotonic() - _docker_cache["at"] < 10 and _docker_cache["value"] is not None:
        return _docker_cache["value"]
    image = os.environ.get("FORGE_SANDBOX_IMAGE", "forge-sandbox")
    if not shutil.which("docker"):
        value = {"status": "error", "detail": "docker CLI not found"}
    else:
        try:
            info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                                  text=True, timeout=5)
            if info.returncode != 0:
                value = {"status": "error", "detail": "docker daemon not reachable: " +
                         metrics.first_line(info.stderr or info.stdout)}
            elif subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                                timeout=5).returncode != 0:
                value = {"status": "error", "detail": f"sandbox image {image!r} not found"}
            else:
                value = {"status": "ok", "server_version": info.stdout.strip(), "image": image}
        except (OSError, subprocess.TimeoutExpired) as exc:
            value = {"status": "error", "detail": f"docker check failed: {exc}"}
    _docker_cache.update(at=time.monotonic(), value=value)
    return value


def health_report() -> tuple[int, dict]:
    components: dict[str, dict] = {}
    try:
        with db.connect() as c:
            c.execute("SELECT 1").fetchone()
        components["database"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        components["database"] = {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
    executor = os.environ.get("FORGE_EXECUTOR") or "docker"
    if executor == "docker":
        components["docker"] = check_docker()
    else:
        components["docker"] = {"status": "skipped", "detail": f"FORGE_EXECUTOR={executor}"}
    ok = all(c["status"] != "error" for c in components.values())
    return (200 if ok else 503), {"status": "ok" if ok else "error", "executor": executor, "components": components,
                                  "time": time.time()}


@ops.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    code, body = await asyncio.to_thread(health_report)
    return JSONResponse(body, status_code=code)


def prometheus_families() -> list[dict]:
    slug = {a["id"]: a["slug"] for a in _rows("SELECT id, slug FROM agents")}
    models = {v["id"]: v["model"] for v in _rows("SELECT id, model FROM agent_versions")}
    runs_total, tokens, cost, durations = [], defaultdict(int), defaultdict(float), defaultdict(list)
    for row in _rows("SELECT agent_id, status, source, COUNT(*) AS n FROM runs GROUP BY agent_id, status, source"):
        runs_total.append(("forge_runs_total", {"agent": slug.get(row["agent_id"], row["agent_id"]),
                                                "status": row["status"], "source": row["source"]}, row["n"]))
    for row in _rows("SELECT agent_id, version_id, SUM(tokens_in) AS tin, SUM(tokens_out) AS tout FROM runs "
                     "GROUP BY agent_id, version_id"):
        a = slug.get(row["agent_id"], row["agent_id"])
        tokens[(a, "input")] += row["tin"] or 0
        tokens[(a, "output")] += row["tout"] or 0
        cost[a] += pricing.cost(models.get(row["version_id"]), row["tin"] or 0, row["tout"] or 0) or 0.0
    for row in _rows("SELECT agent_id, ended_at - started_at AS d FROM runs WHERE ended_at IS NOT NULL "
                     "AND started_at IS NOT NULL"):
        durations[slug.get(row["agent_id"], row["agent_id"])].append(max(0.0, row["d"]))
    hist = [s for a, vals in sorted(durations.items()) for s in metrics.histogram_samples(
        "forge_run_duration_seconds", {"agent": a}, vals)]
    tools = _rows("SELECT json_extract(payload, '$.name') AS tool, coalesce(json_extract(payload, '$.is_error'), 0) "
                  "AS err, COUNT(*) AS n FROM events WHERE type = 'tool_result' GROUP BY 1, 2")
    active = _rows("SELECT COUNT(*) AS n FROM runs WHERE status IN ('queued', 'running')")[0]["n"]
    ims = _rows("SELECT status, COUNT(*) AS n FROM improvements GROUP BY status")
    return [
        {"name": "forge_runs_total", "type": "counter", "help": "Runs by agent, status and source.",
         "samples": runs_total},
        {"name": "forge_run_duration_seconds", "type": "histogram",
         "help": "Run execution time from start to end, by agent.", "samples": hist},
        {"name": "forge_tokens_total", "type": "counter", "help": "Model tokens used by agent runs, by direction.",
         "samples": [("forge_tokens_total", {"agent": a, "direction": d}, n) for (a, d), n in sorted(tokens.items())]},
        {"name": "forge_cost_usd_total", "type": "counter",
         "help": "Estimated model spend of agent runs in USD (self-hosted and unknown prices count as 0).",
         "samples": [("forge_cost_usd_total", {"agent": a}, round(c, 6)) for a, c in sorted(cost.items())]},
        {"name": "forge_tool_calls_total", "type": "counter", "help": "Tool calls by tool name and error flag.",
         "samples": [("forge_tool_calls_total", {"tool": t["tool"] or "unknown", "error": "true" if t["err"] else "false"},
                      t["n"]) for t in tools]},
        {"name": "forge_active_runs", "type": "gauge", "help": "Runs currently queued or running.",
         "samples": [("forge_active_runs", {}, active)]},
        {"name": "forge_improvements_total", "type": "counter", "help": "Improvement cycles by status.",
         "samples": [("forge_improvements_total", {"status": i["status"]}, i["n"]) for i in ims]},
    ]


@ops.get("/metrics", include_in_schema=False)
async def prometheus(request: Request) -> PlainTextResponse:
    if os.environ.get("FORGE_METRICS_PUBLIC") != "1" and not is_local(request):
        raise HTTPException(401, "metrics are only available from localhost (set FORGE_METRICS_PUBLIC=1)")
    text = await asyncio.to_thread(lambda: metrics.format_prometheus(prometheus_families()))
    return PlainTextResponse(text, media_type="text/plain; version=0.0.4; charset=utf-8")

