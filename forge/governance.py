"""Governance for hosted runs: approval prompts for risky tool calls and spending caps.

`RunGovernor` is the `hooks` object the run manager passes to `forge.loop.run_agent`:
- `before_tool(call, send)` applies the version's approval policy (forge/policy.py). A `deny` rule
  returns an error result at once, and so does an `ask` rule in an eval run (source "eval"). An `ask` rule records a pending approval, emits
  `approval_requested`, marks the run `awaiting_approval` and waits on an asyncio Future in the
  manager's `ApprovalRegistry` until a reviewer resolves it or the policy timeout auto-denies it.
- `after_turn(result, send)` prices the cumulative token usage (forge.pricing.cost), stores it as runs.cost_usd,
  emits `budget_warning` at 80% of the per-run cap or monthly budget, and stops the run with status
  `budget_exceeded` once the per-run cap is exceeded.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from forge import budget, db, pricing
from forge.policy import Policy, http_tool_names, match, parse_policy, rule_view
from forge.types import RunResult, ToolCall

log = logging.getLogger("forge.governance")

Send = Callable[[str, dict[str, Any]], Awaitable[None]]
Decision = tuple[str, str, str]  # (approve | deny, note, by)


# ---------------------------------------------------------------- approvals store


def create_approval(run_id: str, call: ToolCall, args: dict, rule: dict, timeout: float) -> dict:
    t = time.time()
    row = {"id": db.new_id("ap"), "run_id": run_id, "tool_call_id": call.id, "name": call.name, "args": args,
           "rule": rule, "status": "pending", "note": "", "resolved_by": None, "created_at": t,
           "expires_at": t + timeout, "resolved_at": None}
    db._insert("approvals", row)
    return row


def get_approval(approval_id: str) -> dict | None:
    return db._one("SELECT * FROM approvals WHERE id = ?", approval_id)


def list_approvals(run_id: str) -> list[dict]:
    return db._all("SELECT * FROM approvals WHERE run_id = ? ORDER BY created_at", run_id)


def finish_approval(approval_id: str, status: str, note: str, by: str) -> None:
    db._update("approvals", "id", approval_id, {"status": status, "note": note, "resolved_by": by,
                                                "resolved_at": time.time()})


def pending_count(agent_id: str) -> int:
    row = db._one("SELECT COUNT(*) AS n FROM approvals a JOIN runs r ON r.id = a.run_id "
                  "WHERE r.agent_id = ? AND a.status = 'pending' AND r.status = 'awaiting_approval'", agent_id)
    return int(row["n"]) if row else 0


def approval_view(a: dict) -> dict:
    keys = ("id", "run_id", "tool_call_id", "name", "args", "rule", "status", "note", "resolved_by", "created_at",
            "expires_at", "resolved_at")
    return {k: a[k] for k in keys}


# ---------------------------------------------------------------- registry


class ApprovalRegistry:
    """Pending approvals of this server process: approval id -> Future resolved with a Decision."""

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[Decision]] = {}

    def open(self, approval_id: str) -> asyncio.Future[Decision]:
        fut: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
        self._futures[approval_id] = fut
        return fut

    def is_pending(self, approval_id: str) -> bool:
        fut = self._futures.get(approval_id)
        return fut is not None and not fut.done()

    def resolve(self, approval_id: str, decision: str, note: str = "", by: str = "ui") -> bool:
        """Delivers a decision. False when the approval is not pending in this process (already decided,
        timed out, or the server restarted)."""
        fut = self._futures.get(approval_id)
        if fut is None or fut.done():
            return False
        fut.set_result((decision, note, by))
        return True

    async def wait(self, approval_id: str, timeout: float) -> Decision:
        fut = self._futures[approval_id]
        try:
            await asyncio.wait({fut}, timeout=timeout)
            if not fut.done():  # no await between the check and set_result, so a reviewer cannot race it
                minutes = timeout / 60
                span = f"{minutes:g} minutes" if timeout >= 60 else f"{timeout:g} seconds"
                fut.set_result(("deny", f"no decision within {span}", "timeout"))
            return fut.result()
        finally:
            self._futures.pop(approval_id, None)


# ---------------------------------------------------------------- run governor


@dataclass
class RunGovernor:
    run_id: str
    agent: dict
    version: dict
    registry: ApprovalRegistry
    secrets: dict[str, str] | None = None
    on_approval_requested: Callable[[dict], Awaitable[None]] | None = None
    source: str | None = None  # the run's source; "eval" runs never pause (see before_tool)
    policy: Policy = field(init=False)
    _warned: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        try:
            self.policy = parse_policy(self.version.get("approval_policy"),
                                       tools=http_tool_names(self.version.get("http_tools")))
        except ValueError:
            log.exception("invalid approval policy on version %s; denying every call", self.version.get("id"))
            self.policy = parse_policy({"rules": [{"tool": "*", "mode": "deny", "label": "invalid policy"}]})

    def _redact(self, value: Any) -> Any:
        from forge.loop import redact

        vals = sorted({v for v in (self.secrets or {}).values() if v}, key=len, reverse=True)
        return redact(value, vals)

    # -------------------------------------------------------- approvals

    async def before_tool(self, call: ToolCall, send: Send) -> tuple[str, bool] | None:
        hit = match(self.policy, call.name, call.args)
        if hit is None:
            return None
        index, rule = hit
        if rule.mode == "deny":
            return f"Denied by policy: {rule.describe()}. This call was not executed.", True
        if self.source == "eval":
            # Evaluations run unattended (and k times per case), so an `ask` rule is an immediate deny there:
            # no approval row, no pause, and the verdict reflects the policy as it would apply with no reviewer.
            return f"Denied by policy during evaluation: {rule.describe()}. This call was not executed.", True
        rv = rule_view(index, rule)
        timeout = float(self.policy.timeout_seconds)
        row = create_approval(self.run_id, call, self._redact(call.args), rv, timeout)
        self.registry.open(row["id"])
        await send("approval_requested", {"id": row["id"], "tool_call_id": call.id, "name": call.name,
                                          "args": call.args, "rule": rv, "expires_at": row["expires_at"]})
        db.update_run(self.run_id, status="awaiting_approval")
        if self.on_approval_requested is not None:
            try:
                await self.on_approval_requested(approval_view(get_approval(row["id"]) or row))
            except Exception:  # noqa: BLE001
                log.exception("approval notification failed for %s", row["id"])
        try:
            decision, note, by = await self.registry.wait(row["id"], timeout)
        finally:
            if (db.get_run(self.run_id) or {}).get("status") == "awaiting_approval":
                db.update_run(self.run_id, status="running")
        approved = decision == "approve"
        status = "approved" if approved else "expired" if by == "timeout" else "denied"
        finish_approval(row["id"], status, note, by)
        await send("approval_resolved", {"id": row["id"], "tool_call_id": call.id,
                                         "decision": "approve" if approved else "deny", "note": note, "by": by})
        if approved:
            return None
        return f"Denied by reviewer: {note or 'no reason given'}. This call was not executed.", True

    # -------------------------------------------------------- spending

    async def _warn(self, scope: str, limit: float, spent: float, send: Send) -> None:
        if scope in self._warned or limit <= 0 or spent + 1e-9 < budget.WARN_FRACTION * limit:
            return
        self._warned.add(scope)
        await send("budget_warning", {"scope": scope, "limit_usd": limit, "spent_usd": round(spent, 6),
                                      "fraction": round(spent / limit, 4)})

    async def after_turn(self, result: RunResult, send: Send) -> tuple[str, str] | None:
        # Same price function as metrics and views (forge.pricing); cache tokens are counted, not priced.
        cost = pricing.cost(self.version["model"], result.tokens_in, result.tokens_out)
        if cost is None:  # unknown price: runs.cost_usd stays NULL and caps cannot apply
            return None
        db.update_run(self.run_id, cost_usd=cost)
        cap = self.version.get("max_run_cost_usd")
        monthly = (db.get_agent(self.agent["id"]) or self.agent).get("monthly_budget_usd")
        if monthly is not None:
            await self._warn("month", float(monthly), budget.month_to_date_usd(self.agent["id"]), send)
        if cap is not None:
            if cost > float(cap):
                return ("budget_exceeded", f"Run stopped: per-run spending cap of {budget.fmt_usd(float(cap))} "
                        f"exceeded ({budget.fmt_usd(cost)} spent).")
            await self._warn("run", float(cap), cost, send)
        return None


# ---------------------------------------------------------------- view extras


def agent_extras(a: dict) -> dict:
    status = budget.budget_status(a)
    return {"monthly_budget_usd": a.get("monthly_budget_usd"), "month_to_date_usd": status["month_to_date_usd"],
            "budget_state": status["state"], "pending_approvals": pending_count(a["id"])}


def governance_summary(a: dict) -> dict:
    return {**budget.budget_status(a), "pending_approvals": pending_count(a["id"]),
            "awaiting_runs": [r["id"] for r in db._all(
                "SELECT id FROM runs WHERE agent_id = ? AND status = 'awaiting_approval' ORDER BY created_at",
                a["id"])]}
