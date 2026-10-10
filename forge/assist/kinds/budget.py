"""Assist kind "budget": a suggested per run cost cap and monthly budget from the agent's recent run costs.

Input: {} (nothing). The server reads the last 30 days of priced runs (drafts excluded) and the grader spend.
Result: {"max_run_cost_usd": number, "monthly_budget_usd": number, "explanation": str, "stats": {"runs",
"mean_usd", "p95_usd", "max_usd", "monthly_usd", "grader_usd", "days"}} (`stats` is computed by the server).
No priced runs yet is a 400.
"""

from __future__ import annotations

import math
import time
from typing import Any

from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register

KIND = "budget"
WINDOW_DAYS = 30

SYSTEM = """You suggest spending limits for an AI agent from its measured costs. A per run cap stops a single \
run once it costs more than the cap (so it must sit comfortably above normal runs and only catch runaway ones). \
A monthly budget blocks new runs once the month's spend (runs plus grading and improvement calls) reaches it.

Suggest:
- max_run_cost_usd: at least the 95th percentile run cost and at least the most expensive normal run, usually \
1.5 to 3 times the 95th percentile, rounded to a friendly number (for example 0.25, 0.5, 1, 2, 5).
- monthly_budget_usd: the projected monthly spend from the recent rate with headroom (usually 1.5 to 2 times), \
never below the per run cap, rounded to a friendly number.
- explanation: two or three plain sentences citing the measured figures (runs, mean, 95th percentile, projected \
monthly spend) and why the limits sit where they do. No dashes as punctuation.

Respond with the JSON object only: {"result": {"max_run_cost_usd": 0.0, "monthly_budget_usd": 0.0, \
"explanation": "..."}, "rationale": "..."}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"max_run_cost_usd": {"type": "number"}, "monthly_budget_usd": {"type": "number"},
                   "explanation": {"type": "string"}},
    "required": ["max_run_cost_usd", "monthly_budget_usd", "explanation"],
    "additionalProperties": False,
}


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(0, min(len(s) - 1, math.ceil(q * len(s)) - 1))
    return s[k]


def load_costs(agent_id: str, since: float) -> tuple[list[tuple[float, float]], float]:
    """([(created_at, cost_usd)] of priced, finished, non draft runs since `since`, grader spend since then)."""
    from forge import db, pricing

    rows = db._all(f"SELECT created_at, cost_usd FROM runs WHERE agent_id = ? AND created_at >= ? AND "
                   f"cost_usd IS NOT NULL AND status NOT IN ('queued', 'running') AND {db.NOT_DRAFT}",
                   agent_id, since)
    grader = 0.0
    for g in db._all("SELECT model, SUM(tokens_in) AS tin, SUM(tokens_out) AS tout FROM grader_calls "
                     "WHERE agent_id = ? AND created_at >= ? GROUP BY model", agent_id, since):
        grader += pricing.cost(g["model"], g["tin"] or 0, g["tout"] or 0) or 0.0
    return [(r["created_at"], float(r["cost_usd"])) for r in rows], grader


def stats_for(costs: list[tuple[float, float]], grader: float, now: float) -> dict[str, Any]:
    vals = [c for _, c in costs]
    first = min((t for t, _ in costs), default=now)
    days = max(1.0, min(float(WINDOW_DAYS), (now - first) / 86400))
    spent = sum(vals) + grader
    return {"runs": len(vals), "mean_usd": round(sum(vals) / len(vals), 6) if vals else 0.0,
            "p95_usd": round(percentile(vals, 0.95), 6), "max_usd": round(max(vals, default=0.0), 6),
            "monthly_usd": round(spent / days * 30, 4), "grader_usd": round(grader, 6), "days": round(days, 1)}


def build_context(req: AssistRequest) -> Prepared:
    assert req.agent is not None and req.version is not None
    now = time.time()
    costs, grader = load_costs(req.agent["id"], now - WINDOW_DAYS * 86400)
    if not any(c > 0 for _, c in costs):
        raise ValueError("no priced runs in the last 30 days yet; run the agent a few times first")
    st = stats_for(costs, grader, now)
    ctx, rendered = C.for_request(req)
    current = (f"per run cap: {req.version.get('max_run_cost_usd')} USD, monthly budget: "
               f"{req.agent.get('monthly_budget_usd')} USD")
    measured = (f"priced runs in the last {st['days']} days: {st['runs']}\nmean run cost: {st['mean_usd']} USD\n"
                f"95th percentile run cost: {st['p95_usd']} USD\nmost expensive run: {st['max_usd']} USD\n"
                f"grading and improvement spend in the window: {st['grader_usd']} USD\n"
                f"projected monthly spend at this rate: {st['monthly_usd']} USD")
    parts = [rendered, f"<current_limits>\n{current}\n</current_limits>", f"<measured_costs>\n{measured}\n</measured_costs>",
             "Suggest the limits as the JSON object."]
    return Prepared(text="\n\n".join(parts), data={"stats": st}, diagnostics={"stats": st})


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    st = data["stats"]
    cap, monthly = result.get("max_run_cost_usd"), result.get("monthly_budget_usd")
    errs = []
    if not isinstance(cap, (int, float)) or isinstance(cap, bool) or cap <= 0:
        errs.append("max_run_cost_usd must be a positive number")
    elif cap < st["p95_usd"]:
        errs.append(f"max_run_cost_usd {cap} is below the 95th percentile run cost {st['p95_usd']}; normal runs "
                    "would be stopped")
    if not isinstance(monthly, (int, float)) or isinstance(monthly, bool) or monthly <= 0:
        errs.append("monthly_budget_usd must be a positive number")
    elif isinstance(cap, (int, float)) and monthly < cap:
        errs.append("monthly_budget_usd must be at least the per run cap")
    elif monthly < st["monthly_usd"]:
        errs.append(f"monthly_budget_usd {monthly} is below the projected monthly spend {st['monthly_usd']}")
    if not str(result.get("explanation") or "").strip():
        errs.append("explanation is empty")
    return errs


def friendly(x: float) -> float:
    """Rounds up to 1, 2 or 5 times a power of ten (0.013 -> 0.02, 3.2 -> 5)."""
    if x <= 0:
        return 0.01
    p = 10 ** math.floor(math.log10(x))
    return round(next(m * p for m in (1, 2, 5, 10) if m * p >= x - 1e-12), 6)


def finalize(result: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    return {**result, "stats": data["stats"]}


def fake(user: str) -> dict[str, Any]:
    import re

    def num(label: str) -> float:
        m = re.search(rf"{label}: ([0-9.eE-]+) USD", user)
        return float(m.group(1)) if m else 0.0

    p95, top, monthly = num("95th percentile run cost"), num("most expensive run"), \
        num("projected monthly spend at this rate")
    cap = friendly(max(p95 * 2, top))
    budget = friendly(max(monthly * 1.5, cap))
    return {"result": {"max_run_cost_usd": cap, "monthly_budget_usd": budget,
                       "explanation": f"Runs cost {p95} USD at the 95th percentile, so a cap of {cap} USD only stops "
                                      f"runaway runs. At the current rate the month costs about {monthly} USD, so "
                                      f"{budget} USD leaves headroom."},
            "rationale": "Limits derived from measured costs."}


SPEC = register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=False, needs_agent=True, fake=fake,
                finalize=finalize)
