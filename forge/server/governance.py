"""Governance HTTP routes: approval decisions, approval listings and the agent spend summary."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge import db, governance
from forge.budget import BudgetExceeded
from forge.server.auth import authorize, share_can_see

router = APIRouter(prefix="/v1")


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "deny"]
    note: str = Field("", max_length=2000)


def _run_agent(run_id: str) -> tuple[dict, dict]:
    r = db.get_run(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    agent = db.get_agent(r["agent_id"])
    assert agent is not None
    return r, agent


@router.get("/runs/{run_id}/approvals")
async def list_run_approvals(run_id: str, request: Request) -> list[dict]:
    r, agent = _run_agent(run_id)
    if authorize(request, agent) == "share" and not share_can_see(request, r):
        raise HTTPException(404, "run not found")  # share tokens see their visitor's share runs only
    return [governance.approval_view(a) for a in governance.list_approvals(run_id)]


@router.post("/runs/{run_id}/approvals/{approval_id}")
async def resolve_approval(run_id: str, approval_id: str, body: ApprovalDecision, request: Request) -> dict:
    _, agent = _run_agent(run_id)
    source = authorize(request, agent)
    if source == "share":
        raise HTTPException(403, "share link users cannot approve or deny tool calls")
    ap = governance.get_approval(approval_id)
    if ap is None or ap["run_id"] != run_id:
        raise HTTPException(404, "approval not found")
    from forge.server.runs import get_manager

    if ap["status"] != "pending" or not get_manager().approvals.resolve(approval_id, body.decision, body.note.strip(),
                                                                        source):
        raise HTTPException(409, f"approval is no longer pending (status {ap['status']})")
    return {**governance.approval_view(ap), "status": "approved" if body.decision == "approve" else "denied",
            "note": body.note.strip(), "resolved_by": source}


@router.get("/agents/{slug}/governance")
async def agent_governance(slug: str, request: Request) -> dict:
    agent = db.get_agent_by_slug(slug)
    if agent is None:
        raise HTTPException(404, f"agent {slug!r} not found")
    authorize(request, agent, allow_share=False)
    return governance.governance_summary(agent)


async def _budget_exceeded(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=402, content={"detail": str(exc)})


def install(app: FastAPI) -> None:
    """Adds the routes and the 402 handler. Call before the SPA catch-all route is registered."""
    app.include_router(router)
    app.add_exception_handler(BudgetExceeded, _budget_exceeded)
