"""Spending caps: run cost, month-to-date spend and the monthly budget check.

Every cost comes from `forge.pricing.cost` (the one price table, with the FORGE_PRICES override), so
metrics, the stored run cost (`runs.cost_usd`) and budgets always agree. Prompt-cache tokens are counted
but not priced, as in forge.pricing.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import time
from typing import Any

from forge import pricing

WARN_FRACTION = 0.8


# ---------------------------------------------------------------- month-to-date spend


def month_start(now: float | None = None) -> float:
    """Unix time of the first instant of the current calendar month (UTC)."""
    t = _dt.datetime.fromtimestamp(now if now is not None else time.time(), _dt.UTC)
    return calendar.timegm((t.year, t.month, 1, 0, 0, 0))


def month_to_date_usd(agent_id: str, now: float | None = None) -> float:
    """This calendar month's spend: the stored cost of every run (runs.cost_usd; a child run started by an
    agent tool is its own row, so nothing is counted twice) plus judge and optimizer calls (grader_calls),
    which run outside any agent run."""
    from forge import db

    since = month_start(now)
    row = db._one("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM runs WHERE agent_id = ? AND created_at >= ?",
                  agent_id, since)
    spent = float(row["s"] if row else 0.0)
    for g in db._all("SELECT model, SUM(tokens_in) AS tin, SUM(tokens_out) AS tout FROM grader_calls "
                     "WHERE agent_id = ? AND created_at >= ? GROUP BY model", agent_id, since):
        spent += pricing.cost(g["model"], g["tin"] or 0, g["tout"] or 0) or 0.0
    return spent


def fmt_usd(x: float) -> str:
    return f"${x:,.2f}" if x >= 0.01 or x == 0 else f"${x:,.4f}"


class BudgetExceeded(Exception):
    """Raised before a run starts when the agent's month-to-date spend is at or over its monthly budget."""

    def __init__(self, budget: float, spent: float) -> None:
        self.budget, self.spent = budget, spent
        super().__init__(
            f"Monthly budget of {fmt_usd(budget)} reached for this agent ({fmt_usd(spent)} spent this month). "
            "Raise the budget in Configure or wait until next month.")


def budget_status(agent: dict[str, Any]) -> dict[str, Any]:
    budget = agent.get("monthly_budget_usd")
    spent = month_to_date_usd(agent["id"])
    if budget is None:
        return {"monthly_budget_usd": None, "month_to_date_usd": round(spent, 6), "fraction": None, "state": "none"}
    frac = spent / budget if budget > 0 else 1.0
    state = "exceeded" if spent >= budget else "warning" if frac + 1e-9 >= WARN_FRACTION else "ok"
    return {"monthly_budget_usd": budget, "month_to_date_usd": round(spent, 6), "fraction": frac, "state": state}


def ensure_budget(agent: dict[str, Any]) -> None:
    """Raises BudgetExceeded when the agent is at or over its monthly budget."""
    budget = agent.get("monthly_budget_usd")
    if budget is None:
        return
    spent = month_to_date_usd(agent["id"])
    if spent >= float(budget):
        raise BudgetExceeded(float(budget), spent)
