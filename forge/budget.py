"""Spending caps: run cost estimates, month-to-date spend and the monthly budget check.

Cost comes from token usage and a per-model price table (USD per million tokens, input and output).

MERGE NOTE (feat/governance): another branch (feat/observability) adds `forge/pricing.py` with the
canonical price table. `estimate_cost_usd` uses it when importable and otherwise falls back to
`FALLBACK_PRICES` below. When both branches are merged, unify by deleting `FALLBACK_PRICES` and
`_fallback_price` and keeping only the `forge.pricing` path (the FORGE_PRICES override stays).
"""

from __future__ import annotations

import calendar
import datetime as _dt
import json
import logging
import os
import time
from typing import Any

log = logging.getLogger("forge.budget")

WARN_FRACTION = 0.8

# ---------------------------------------------------------------- fallback price table (see MERGE NOTE)
# USD per million tokens: (input, output). Keys are model ids without the provider prefix; a dated
# variant such as "claude-opus-5-5-20261001" matches by prefix.
FALLBACK_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
FREE_PROVIDERS = ("openai_compat", "fake")  # self-hosted and scripted models cost nothing


def _env_prices() -> dict[str, tuple[float, float]]:
    """FORGE_PRICES='{"<model string or id>": [in_per_million, out_per_million], ...}' (or {"in":..,"out":..})."""
    raw = os.environ.get("FORGE_PRICES")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("FORGE_PRICES is not valid JSON; ignoring it")
        return {}
    out: dict[str, tuple[float, float]] = {}
    for k, v in (data.items() if isinstance(data, dict) else []):
        try:
            if isinstance(v, dict):
                out[str(k)] = (float(v.get("in", 0)), float(v.get("out", 0)))
            else:
                out[str(k)] = (float(v[0]), float(v[1]))
        except (TypeError, ValueError, IndexError, KeyError):
            log.warning("FORGE_PRICES entry %r is invalid; ignoring it", k)
    return out


def _lookup(table: dict[str, tuple[float, float]], model: str) -> tuple[float, float] | None:
    _, _, model_id = model.partition(":")
    for key in (model, model_id):
        if key and key in table:
            return table[key]
    for key in sorted(table, key=len, reverse=True):
        if model_id and model_id.startswith(key):
            return table[key]
    return None


def _fallback_price(model: str) -> tuple[float, float] | None:
    if model.partition(":")[0] in FREE_PROVIDERS:
        return (0.0, 0.0)
    return _lookup(FALLBACK_PRICES, model)


def _pricing_module_cost(model: str, tokens_in: int, tokens_out: int) -> tuple[bool, float | None]:
    """(handled, cost) from forge.pricing when that module exists and exposes a usable function."""
    try:
        from forge import pricing  # type: ignore[attr-defined]
    except ImportError:
        return False, None
    for name in ("estimate_cost_usd", "cost_usd", "estimate_cost"):
        fn = getattr(pricing, name, None)
        if callable(fn):
            try:
                value = fn(model, tokens_in, tokens_out)
            except Exception:  # noqa: BLE001
                log.exception("forge.pricing.%s failed; using the fallback table", name)
                return False, None
            return True, None if value is None else float(value)
    return False, None


def estimate_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Estimated USD cost of the given token usage, or None when the model's price is unknown."""
    override = _lookup(_env_prices(), model)
    if override is not None:
        return (tokens_in * override[0] + tokens_out * override[1]) / 1_000_000
    handled, cost = _pricing_module_cost(model, tokens_in, tokens_out)
    if handled:
        return cost
    price = _fallback_price(model)
    if price is None:
        return None
    return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000


# ---------------------------------------------------------------- month-to-date spend


def month_start(now: float | None = None) -> float:
    """Unix time of the first instant of the current calendar month (UTC)."""
    t = _dt.datetime.fromtimestamp(now if now is not None else time.time(), _dt.UTC)
    return calendar.timegm((t.year, t.month, 1, 0, 0, 0))


def month_to_date_usd(agent_id: str, now: float | None = None) -> float:
    from forge import db

    row = db._one("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM runs WHERE agent_id = ? AND created_at >= ?",
                  agent_id, month_start(now))
    return float(row["s"] if row else 0.0)


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
