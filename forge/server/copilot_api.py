"""Forge assistant routes (see CONTRACT.md "Assistant").

Builder only: localhost, or any agent's API key (that caller's tools then act with that key, so only on its own
agent). Share tokens and anonymous remote callers get 401. A caller sees only its own sessions.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from forge import db
from forge.copilot import engine
from forge.copilot.api_client import ForgeApi
from forge.server.auth import _bearer, is_local

router = APIRouter(prefix="/v1/copilot")


class Context(BaseModel):
    page: str | None = Field(default=None, max_length=300)
    agent_slug: str | None = Field(default=None, max_length=80)
    run_id: str | None = Field(default=None, max_length=40)
    version: int | str | None = None


class SessionIn(BaseModel):
    context: Context = Field(default_factory=Context)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=engine.MAX_USER_CHARS)
    context: Context = Field(default_factory=Context)


class ConfirmIn(BaseModel):
    confirm_id: str
    approve: bool


def caller(request: Request) -> tuple[str, ForgeApi]:
    """(owner, api client acting with the caller's rights) or 401."""
    key = _bearer(request)
    if is_local(request):
        return "local", ForgeApi(local=True)
    if key:
        agent = db.get_agent_by_key_hash(db.hash_key(key))
        if agent is not None:
            return f"key:{agent['id']}", ForgeApi(local=False, authorization=f"Bearer {key}")
    raise HTTPException(401, "the assistant is only available to builders (localhost or an agent API key)")


def session_or_404(session_id: str, owner: str) -> dict:
    s = db.get_copilot_session(session_id)
    if s is None or s["owner"] != owner:
        raise HTTPException(404, "session not found")
    return s


def session_view(s: dict) -> dict:
    return {"id": s["id"], "title": s["title"], "context": s.get("context") or {}, "created_at": s["created_at"],
            "updated_at": s["updated_at"], "running": engine.active(s["id"]) is not None}


def message_view(m: dict) -> dict:
    payload = dict(m["payload"] or {})
    payload.pop("model_text", None)  # internal: the text the model saw
    return {"seq": m["seq"], "type": m["type"], "payload": payload, "created_at": m["created_at"]}


async def sse(events: AsyncIterator[dict]) -> AsyncIterator[dict]:
    async for ev in events:
        payload = dict(ev["payload"])
        payload.pop("model_text", None)
        yield {"event": ev["type"], "id": str(ev["seq"]), "data": json.dumps({"seq": ev["seq"], "type": ev["type"],
                                                                              "payload": payload})}
    yield {"event": "end", "data": "{}"}


@router.post("/sessions")
async def create_session(request: Request, body: SessionIn = Body(default_factory=SessionIn)) -> dict:
    owner, _ = caller(request)
    return session_view(db.create_copilot_session(owner, context=body.context.model_dump(exclude_none=True)))


@router.get("/sessions")
async def list_sessions(request: Request, limit: int = 10) -> list[dict]:
    owner, _ = caller(request)
    return [session_view(s) for s in db.list_copilot_sessions(owner, max(1, min(limit, 50)))]


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict:
    owner, _ = caller(request)
    s = session_or_404(session_id, owner)
    pending = engine.pending_confirm(session_id)
    return {**session_view(s), "messages": [message_view(m) for m in db.list_copilot_messages(session_id)],
            "pending_confirm": pending["payload"] if pending else None}


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: MessageIn, request: Request) -> EventSourceResponse:
    owner, api = caller(request)
    s = session_or_404(session_id, owner)
    try:
        turn = engine.start(s, body.text.strip(), body.context.model_dump(exclude_none=True), api)
    except engine.Busy as exc:
        raise HTTPException(409, str(exc)) from exc
    return EventSourceResponse(sse(turn.stream()), ping=15)


@router.get("/sessions/{session_id}/events")
async def stream_events(session_id: str, request: Request, after: int = -1) -> EventSourceResponse:
    """Resumes the latest turn's stream after `seq` (for a reload while it runs); ends at once when idle."""
    owner, _ = caller(request)
    session_or_404(session_id, owner)
    turn = engine.latest(session_id)

    async def stored() -> AsyncIterator[dict]:
        for m in db.list_copilot_messages(session_id, after):
            yield m

    return EventSourceResponse(sse(turn.stream(after) if turn is not None else stored()), ping=15)


@router.post("/sessions/{session_id}/confirm")
async def confirm(session_id: str, body: ConfirmIn, request: Request) -> dict[str, Any]:
    owner, _ = caller(request)
    session_or_404(session_id, owner)
    try:
        engine.resolve(session_id, body.confirm_id, body.approve)
    except KeyError:
        raise HTTPException(404, "confirmation not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "confirm_id": body.confirm_id, "approved": body.approve}


@router.post("/sessions/{session_id}/cancel")
async def cancel(session_id: str, request: Request) -> dict[str, Any]:
    owner, _ = caller(request)
    session_or_404(session_id, owner)
    return {"ok": True, "cancelled": engine.cancel(session_id)}
