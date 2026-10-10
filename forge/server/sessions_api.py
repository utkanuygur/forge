"""Sessions and run cancel (prefix /v1). See CONTRACT.md "Sessions" and "Share and API conversations".

The builder (localhost) gets Playground sessions; an agent's API key gets the same endpoints (channel `api`, turns
with source `api`); a share token gets conversations on the production version that belong to one share page
visitor (channel `share`, turns with source `share`), within the share link limits of forge/share_limits.py.
Slack threads reuse `start_turn` through forge/triggers/core.py (channel `slack`).
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from forge import db, share_limits
from forge.budget import BudgetExceeded
from forge.server import runs
from forge.server.auth import authorize, share_can_see, visitor
from forge.server.views import run_view, version_view
from forge.sessions import build_history, read_archive
from forge.types import HttpTool, TERMINAL_STATUSES

router = APIRouter(prefix="/v1")

TERMINAL = TERMINAL_STATUSES  # a turn awaiting approval is still running
DRAFT_FIELDS = ("system_prompt", "lessons", "model", "tools", "http_tools", "network", "seed_files")
CHANNEL = {"ui": "playground", "api": "api", "share": "share"}  # caller source -> session channel
SHARE_BUDGET_MESSAGE = "This agent has reached its usage limit for this month. Try again later."
EXPIRED_MESSAGE = ("This conversation expired after {hours} of inactivity and is now read only. "
                   "Start a new conversation to keep going.")


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


# ---------------------------------------------------------------- errors


def limit_response(exc: share_limits.LimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=429, headers={"Retry-After": str(exc.retry_after)},
                        content={"detail": exc.detail, "limit": exc.limit, "retry_after": exc.retry_after})


async def _limit_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, share_limits.LimitExceeded)
    return limit_response(exc)


def install(app: Any) -> None:
    """Registers the 429 handler for share limits."""
    app.add_exception_handler(share_limits.LimitExceeded, _limit_handler)


@contextlib.contextmanager
def share_budget_message(source: str) -> Iterator[None]:
    """Share visitors get a plain 402 without the builder's budget figures or Configure hints."""
    try:
        yield
    except BudgetExceeded as exc:
        if source != "share":
            raise
        raise HTTPException(402, SHARE_BUDGET_MESSAGE) from exc


def _hours(seconds: float) -> str:
    h = seconds / 3600
    return f"{h:g} hour" + ("" if h == 1 else "s")


# ---------------------------------------------------------------- lookups


def _agent(slug: str, request: Request) -> tuple[dict, str]:
    agent = db.get_agent_by_slug(slug)
    if agent is None:
        raise HTTPException(404, f"agent {slug!r} not found")
    return agent, authorize(request, agent)


def _visible(s: dict, source: str, request: Request) -> bool:
    if source != "share":
        return True
    vid = visitor(request)
    return s.get("channel") == "share" and vid is not None and s.get("visitor_id") == vid


def _session(session_id: str, request: Request) -> tuple[dict, dict, str]:
    s = db.get_session(session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    agent = db.get_agent(s["agent_id"])
    assert agent is not None
    source = authorize(request, agent)
    if not _visible(s, source, request):  # another visitor's or the builder's session: not even confirmed
        raise HTTPException(404, "session not found")
    return s, agent, source


def session_config(s: dict) -> dict:
    """The version dict a session's turns run with: the version itself, or the draft over its base."""
    base = db.get_version(s["version_id"] or s["base_version_id"])
    assert base is not None
    if s["version_id"]:
        return base
    draft = {k: v for k, v in (s["draft_config"] or {}).items() if k in DRAFT_FIELDS and v is not None}
    return {**base, **draft}


def expiring(s: dict) -> bool:
    """Share and Slack conversations expire when idle; Playground and API sessions do not."""
    return s.get("channel") in ("share", "slack")


def session_view(s: dict, *, turns: bool = False, share: bool = False) -> dict:
    v = db.get_version(s["version_id"]) if s["version_id"] else None
    base = db.get_version(s["base_version_id"])
    agent = db.get_agent(s["agent_id"])
    # Contract fields first; base_version_n, is_draft, turn_count and running are additive extras.
    out: dict[str, Any] = {
        "id": s["id"], "agent_slug": agent["slug"] if agent else None, "agent_id": s["agent_id"],
        "version_n": v["n"] if v else None,
        "base_version_n": base["n"] if base else None, "draft": s["draft_config"], "is_draft": v is None,
        "title": s["title"], "created_at": s["created_at"], "updated_at": s["updated_at"],
        "turn_count": s.get("turn_count"), "channel": s.get("channel") or "playground",
    }
    if expiring(s):
        out["expires_at"] = share_limits.expires_at(s)
        out["expired"] = share_limits.is_expired(s)
    if agent and s.get("channel") == "share":
        out["max_turns"] = share_limits.effective(agent)["session_turns"]
    if turns:
        rows = db.session_runs(s["id"])
        out["turns"] = [run_view(r) for r in rows]
        out["turn_count"] = len(rows)
        out["running"] = any(r["status"] not in TERMINAL for r in rows)
        if out.get("expired") and out["running"]:
            out["expired"] = False  # a turn still running keeps it open
    if share:  # share visitors never see configuration: no draft or base version details
        for k in ("draft", "base_version_n", "is_draft", "agent_id"):
            out.pop(k, None)
    return out


# ---------------------------------------------------------------- turns


def prepare_turn(s: dict) -> tuple[list, dict[str, bytes] | None, int]:
    """History, workspace and turn index for the next turn; 409 while a turn is still running."""
    prev = db.session_runs(s["id"])
    if any(r["status"] not in TERMINAL for r in prev):
        raise HTTPException(409, "a turn is still running; stop it or wait for it to finish")
    history = build_history([(r["input"], db.list_events(r["id"]), r["status"]) for r in prev])
    workspace = None
    for r in reversed(prev):  # continue from the most recent turn that exported its workspace
        if r["artifact_path"] and Path(r["artifact_path"]).exists():
            workspace = read_archive(r["artifact_path"])
            break
    return history, workspace, len(prev)


async def start_turn(s: dict, agent: dict, text: str, source: str, *, version: dict | None = None,
                     check: bool = False) -> str:
    """Starts the next turn of session `s` as a run with `source`. `check` applies the share link limits;
    nothing awaits between that check and the run row being created (see share_limits.check_turn)."""
    history, workspace, index = prepare_turn(s)
    if check:
        share_limits.check_turn(agent, s)
    with share_budget_message(source):
        run_id = await runs.get_manager().start_run(
            agent, version or session_config(s), text, source, session_id=s["id"], turn_index=index,
            history=history or None, workspace=workspace,
            visitor_id=s.get("visitor_id") if source == "share" else None)
    fields: dict[str, Any] = {"updated_at": db.now()}
    if not s["title"]:
        fields["title"] = text[:80]
    if version and s["version_id"] and version["id"] != s["version_id"]:
        fields["version_id"] = version["id"]
    db.update_session(s["id"], **fields)
    return run_id


# ---------------------------------------------------------------- routes


@router.post("/agents/{slug}/sessions")
async def create_session(slug: str, request: Request, response: Response,
                         body: SessionCreate = Body(default_factory=SessionCreate)) -> dict:
    agent, source = _agent(slug, request)
    if source == "share":
        if body.version is not None or body.draft is not None:
            raise HTTPException(400, "share link conversations always use the production version")
        prod = db.get_version_n(agent["id"], "prod")
        if prod is None:
            raise HTTPException(404, "this agent has no production version")
        vid = visitor(request)
        if not share_limits.known_visitor(agent["id"], vid):
            vid = share_limits.new_visitor(agent["id"])
        assert vid is not None
        share_limits.check_new_session(agent, vid)
        s = db.create_session(agent["id"], prod["id"], version_id=prod["id"], title=body.title[:200],
                              channel="share", visitor_id=vid)
        response.headers["X-Forge-Visitor"] = vid
        return {**session_view(s, turns=True, share=True), "visitor_id": vid}
    n: int | str = body.version if body.version is not None else "prod"
    base = db.get_version_n(agent["id"], n)
    if base is None:
        raise HTTPException(404, f"version {n} not found")
    channel = CHANNEL[source]
    if body.draft is not None:
        draft = body.draft.model_dump(exclude_none=True)
        if draft.get("http_tools"):
            draft["http_tools"] = [t if isinstance(t, dict) else t.model_dump() for t in draft["http_tools"]]
        s = db.create_session(agent["id"], base["id"], version_id=None, draft_config=draft, title=body.title,
                              channel=channel)
    else:
        s = db.create_session(agent["id"], base["id"], version_id=base["id"], title=body.title, channel=channel)
    return session_view(s, turns=True)


@router.get("/agents/{slug}/sessions")
async def list_sessions(slug: str, request: Request, limit: int = Query(20, ge=1, le=200),
                        channel: Literal["playground", "api", "share", "slack", "all"] | None = None) -> list[dict]:
    """Builder and API callers see their own channel by default (`?channel=` picks another, or `all`);
    a share visitor sees only its own conversations."""
    agent, source = _agent(slug, request)
    if source == "share":
        vid = visitor(request)
        if not vid:
            return []
        return [session_view(s, share=True)
                for s in db.list_sessions(agent["id"], limit, channel="share", visitor_id=vid)]
    ch = channel or CHANNEL[source]
    return [session_view(s) for s in db.list_sessions(agent["id"], limit, channel=None if ch == "all" else ch)]


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict:
    s, _, source = _session(session_id, request)
    return session_view(s, turns=True, share=source == "share")


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: MessageIn, request: Request) -> dict:
    s, agent, source = _session(session_id, request)
    text = body.input.strip()
    if not text:
        raise HTTPException(400, "input is required")
    version = None
    if source == "share":
        if share_limits.is_expired(s) and not any(r["status"] not in TERMINAL for r in db.session_runs(s["id"])):
            raise HTTPException(410, EXPIRED_MESSAGE.format(hours=_hours(share_limits.idle_seconds())))
        version = db.get_version_n(agent["id"], "prod")  # share turns always run the current production version
        if version is None:
            raise HTTPException(404, "this agent has no production version")
    run_source = "playground" if source == "ui" else source
    run_id = await start_turn(s, agent, text, run_source, version=version, check=source == "share")
    if body.wait:
        await runs.get_manager().wait(run_id)
    return run_view(db.get_run(run_id))  # type: ignore[arg-type]


@router.post("/sessions/{session_id}/save-version")
async def save_draft(session_id: str, request: Request, body: SaveDraft = Body(default_factory=SaveDraft)) -> dict:
    """Create a new agent version from a draft session's config (it is not promoted)."""
    s = db.get_session(session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    agent = db.get_agent(s["agent_id"])
    assert agent is not None
    authorize(request, agent, allow_share=False)
    if s["version_id"]:
        raise HTTPException(400, "only draft sessions can be saved as a version")
    cfg = session_config(s)
    # The draft fields over everything else of the base (approval policy, run cap, MCP servers, callable agents).
    carried = ("approval_policy", "max_run_cost_usd", "mcp_servers", "callable_agents")
    v = db.create_version(agent["id"], **{k: cfg.get(k) for k in (*DRAFT_FIELDS, *carried)},
                          parent_id=s["base_version_id"],
                          created_by="human", rationale=body.rationale or "Saved from a playground draft")
    return version_view(v)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict:
    """Stop a running run. Idempotent for an already cancelled run; 409 for one that finished otherwise.
    A share visitor may stop only its own share runs."""
    r = db.get_run(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    source = authorize(request, db.get_agent(r["agent_id"]))  # type: ignore[arg-type]
    if source == "share" and not (share_can_see(request, r) and r.get("visitor_id")):
        raise HTTPException(404, "run not found")
    if r["status"] == "cancelled":
        return run_view(r)
    if r["status"] in TERMINAL:
        raise HTTPException(409, f"run already finished ({r['status']})")
    mgr = runs.get_manager()
    if not mgr.cancel(run_id) and run_id not in mgr.cancelled:
        raise HTTPException(409, "this run is not running on this server (local runs stop from the CLI)")
    await mgr.wait(run_id)
    return run_view(db.get_run(run_id))  # type: ignore[arg-type]
