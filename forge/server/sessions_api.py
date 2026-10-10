"""Playground sessions and run cancel (builder endpoints, prefix /v1). See CONTRACT.md "Sessions"."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel

from forge import db
from forge.server import runs
from forge.server.auth import authorize
from forge.server.views import run_view, version_view
from forge.sessions import build_history, read_archive
from forge.types import HttpTool

router = APIRouter(prefix="/v1")

TERMINAL = {"succeeded", "failed", "timeout", "cancelled"}
DRAFT_FIELDS = ("system_prompt", "lessons", "model", "tools", "http_tools", "network", "seed_files")


class DraftConfig(BaseModel):
    system_prompt: str | None = None
    lessons: list[str] | None = None
    model: str | None = None
    tools: list[str] | None = None
    http_tools: list[HttpTool] | None = None
    network: bool | None = None
    seed_files: dict[str, str] | None = None


class SessionCreate(BaseModel):
    version: int | None = None  # default: prod; ignored for drafts except as the base
    draft: DraftConfig | None = None
    title: str = ""


class MessageIn(BaseModel):
    input: str
    wait: bool = False


class SaveDraft(BaseModel):
    rationale: str = ""


def _agent(slug: str, request: Request) -> dict:
    agent = db.get_agent_by_slug(slug)
    if agent is None:
        raise HTTPException(404, f"agent {slug!r} not found")
    authorize(request, agent, allow_share=False)
    return agent


def _session(session_id: str, request: Request) -> tuple[dict, dict]:
    s = db.get_session(session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    agent = db.get_agent(s["agent_id"])
    assert agent is not None
    authorize(request, agent, allow_share=False)
    return s, agent


def session_config(s: dict) -> dict:
    """The version dict a session's turns run with: the version itself, or the draft over its base."""
    base = db.get_version(s["version_id"] or s["base_version_id"])
    assert base is not None
    if s["version_id"]:
        return base
    draft = {k: v for k, v in (s["draft_config"] or {}).items() if k in DRAFT_FIELDS and v is not None}
    return {**base, **draft}


def session_view(s: dict, *, turns: bool = False) -> dict:
    v = db.get_version(s["version_id"]) if s["version_id"] else None
    base = db.get_version(s["base_version_id"])
    agent = db.get_agent(s["agent_id"])
    # Contract fields first; base_version_n, is_draft, turn_count and running are additive extras.
    out: dict[str, Any] = {
        "id": s["id"], "agent_slug": agent["slug"] if agent else None, "agent_id": s["agent_id"],
        "version_n": v["n"] if v else None,
        "base_version_n": base["n"] if base else None, "draft": s["draft_config"], "is_draft": v is None,
        "title": s["title"], "created_at": s["created_at"], "updated_at": s["updated_at"],
        "turn_count": s.get("turn_count"),
    }
    if turns:
        rows = db.session_runs(s["id"])
        out["turns"] = [run_view(r) for r in rows]
        out["turn_count"] = len(rows)
        out["running"] = any(r["status"] not in TERMINAL for r in rows)
    return out


@router.post("/agents/{slug}/sessions")
async def create_session(slug: str, request: Request,
                         body: SessionCreate = Body(default_factory=SessionCreate)) -> dict:
    agent = _agent(slug, request)
    n: int | str = body.version if body.version is not None else "prod"
    base = db.get_version_n(agent["id"], n)
    if base is None:
        raise HTTPException(404, f"version {n} not found")
    if body.draft is not None:
        draft = body.draft.model_dump(exclude_none=True)
        if draft.get("http_tools"):
            draft["http_tools"] = [t if isinstance(t, dict) else t.model_dump() for t in draft["http_tools"]]
        s = db.create_session(agent["id"], base["id"], version_id=None, draft_config=draft, title=body.title)
    else:
        s = db.create_session(agent["id"], base["id"], version_id=base["id"], title=body.title)
    return session_view(s, turns=True)


@router.get("/agents/{slug}/sessions")
async def list_sessions(slug: str, request: Request, limit: int = Query(20, ge=1, le=200)) -> list[dict]:
    agent = _agent(slug, request)
    return [session_view(s) for s in db.list_sessions(agent["id"], limit)]


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict:
    s, _ = _session(session_id, request)
    return session_view(s, turns=True)


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: MessageIn, request: Request) -> dict:
    s, agent = _session(session_id, request)
    text = body.input.strip()
    if not text:
        raise HTTPException(400, "input is required")
    prev = db.session_runs(session_id)
    if any(r["status"] not in TERMINAL for r in prev):
        raise HTTPException(409, "a turn is still running; stop it or wait for it to finish")
    history = build_history([(r["input"], db.list_events(r["id"]), r["status"]) for r in prev])
    workspace = None
    for r in reversed(prev):  # continue from the most recent turn that exported its workspace
        if r["artifact_path"] and Path(r["artifact_path"]).exists():
            workspace = read_archive(r["artifact_path"])
            break
    run_id = await runs.get_manager().start_run(
        agent, session_config(s), text, "playground", session_id=session_id, turn_index=len(prev),
        history=history or None, workspace=workspace)
    db.update_session(session_id, updated_at=db.now(), **({} if s["title"] else {"title": text[:80]}))
    if body.wait:
        await runs.get_manager().wait(run_id)
    return run_view(db.get_run(run_id))  # type: ignore[arg-type]


@router.post("/sessions/{session_id}/save-version")
async def save_draft(session_id: str, request: Request, body: SaveDraft = Body(default_factory=SaveDraft)) -> dict:
    """Create a new agent version from a draft session's config (it is not promoted)."""
    s, agent = _session(session_id, request)
    if s["version_id"]:
        raise HTTPException(400, "only draft sessions can be saved as a version")
    cfg = session_config(s)
    v = db.create_version(agent["id"], **{k: cfg[k] for k in DRAFT_FIELDS}, parent_id=s["base_version_id"],
                          created_by="human", rationale=body.rationale or "Saved from a playground draft")
    return version_view(v)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict:
    """Stop a running run. Idempotent for an already cancelled run; 409 for one that finished otherwise."""
    r = db.get_run(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    authorize(request, db.get_agent(r["agent_id"]), allow_share=False)  # type: ignore[arg-type]
    if r["status"] == "cancelled":
        return run_view(r)
    if r["status"] in TERMINAL:
        raise HTTPException(409, f"run already finished ({r['status']})")
    mgr = runs.get_manager()
    if not mgr.cancel(run_id) and run_id not in mgr.cancelled:
        raise HTTPException(409, "this run is not running on this server (local runs stop from the CLI)")
    await mgr.wait(run_id)
    return run_view(db.get_run(run_id))  # type: ignore[arg-type]


