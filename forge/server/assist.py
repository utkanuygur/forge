"""Assist routes: `POST /v1/assist/{kind}` for every registered kind, and the `sample-task` alias for kind "task".

Builders (localhost or the agent's API key) may call any kind; share-token callers only kinds registered with
`share_allowed`, always on prod, rate limited in memory per token across all kinds, and they only ever get
{result, rationale}. Every call with an agent is one metered grader call (grader_calls kind "assist:<kind>").
See CONTRACT.md "Assist".
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from forge import assist, db
from forge.assist import engine, registry
from forge.server import evals
from forge.server.auth import authorize, is_local

log = logging.getLogger("forge.assist")
router = APIRouter(prefix="/v1")

SHARE_LIMIT_DEFAULT = 10
SHARE_WINDOW_S = 3600.0


def metered_kind(kind: str) -> str:
    return f"assist:{kind}"  # grader_calls.kind


class AssistIn(BaseModel):
    slug: str | None = None
    version: int | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    avoid: list[str] = Field(default_factory=list, max_length=50)


class SampleTaskIn(BaseModel):
    version: int | None = None
    hint: str | None = Field(default=None, max_length=2000)
    avoid: list[str] = Field(default_factory=list, max_length=50)


# ---------------------------------------------------------------- share rate limit


class RateLimiter:
    """At most `limit` calls per `window` seconds per key, in memory (resets when the server restarts)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> float | None:
        """Records a hit and returns None when allowed, else the seconds until the next call is allowed."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(0.0, q[0] + self.window - now)
            q.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def _share_limit() -> int:
    try:
        return max(1, int(os.environ.get("FORGE_ASSIST_SHARE_LIMIT") or SHARE_LIMIT_DEFAULT))
    except ValueError:
        return SHARE_LIMIT_DEFAULT


share_limiter = RateLimiter(_share_limit(), SHARE_WINDOW_S)  # one budget per share token, shared by all kinds


# ---------------------------------------------------------------- core

RECENT_RESULTS = 10
# The last results per (agent id, kind), in memory, shown to the model as earlier suggestions so consecutive
# calls differ even when the caller sends no avoid list (API callers) and before anything was run.
_recent_results: dict[tuple[str, str], deque[str]] = defaultdict(lambda: deque(maxlen=RECENT_RESULTS))


def _generation_count(agent_id: str, kind: str) -> int:
    with db.connect() as c:
        row = c.execute("SELECT COUNT(*) FROM grader_calls WHERE agent_id = ? AND kind = ?",
                        (agent_id, metered_kind(kind))).fetchone()
    return int(row[0]) if row else 0


async def run_kind(kind: str, request: Request, *, slug: str | None, version: int | None, input: dict[str, Any],
                   avoid: list[str]) -> tuple[dict[str, Any], str, dict | None, registry.Prepared]:
    """Resolves auth and context, makes the call. Returns (reply, source, version, prepared)."""
    spec = assist.get(kind)
    if spec is None:
        raise HTTPException(404, f"unknown assist kind {kind!r}; known: {', '.join(assist.kinds())}")
    agent = ver = None
    if slug:
        agent = db.get_agent_by_slug(slug)
        if agent is None:
            raise HTTPException(404, f"agent {slug!r} not found")
        source = authorize(request, agent, allow_share=spec.share_allowed)
    elif spec.needs_agent:
        raise HTTPException(400, f"assist kind {kind!r} needs slug")
    elif is_local(request):
        source = "ui"
    else:
        raise HTTPException(401, "assist without an agent is only available from localhost")
    if source == "share":
        wait = share_limiter.check(agent["share_token"])  # type: ignore[index]
        if wait is not None:
            raise HTTPException(429, f"Too many suggestions from this link. Try again in {math.ceil(wait / 60)} min.",
                                headers={"Retry-After": str(math.ceil(wait))})
    secrets: dict[str, str] = {}
    req = registry.AssistRequest(kind=kind, input=input, avoid=[a for a in avoid if isinstance(a, str)],
                                 source=source)
    if agent is not None:
        from forge import budget

        budget.ensure_budget(agent)  # metered agent spend: 402 once the monthly budget is used up
        n = version if version is not None and source != "share" else "prod"  # share links use prod
        ver = db.get_version_n(agent["id"], n)
        if ver is None:
            raise HTTPException(404, f"version {version} not found")
        secrets = db.get_secrets(agent["id"])
        req.agent, req.version, req.secret_names = agent, ver, list(secrets)
        req.recent_inputs = [r["input"] for r in db.list_runs(agent["id"], limit=60)]
        req.generation = _generation_count(agent["id"], kind)
        req.recent_results = list(reversed(_recent_results[(agent["id"], kind)]))
    try:
        prepared = spec.build_context(req)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    grader: Any = engine.structured(spec, evals.grader())
    if agent is not None:
        grader = evals.MeteredProvider(grader, evals.grader_model(), agent["id"], metered_kind(kind))
    try:
        reply = await engine.run_assist(spec, prepared, grader)
    except engine.AssistError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001  provider or network failure
        log.exception("assist %s failed for %s", kind, slug)
        raise HTTPException(502, f"The assist model call failed: {type(exc).__name__}: {exc}") from exc
    reply = engine.redact(reply, list(secrets.values()))
    if agent is not None and spec.result_text is not None and (text := spec.result_text(reply["result"])):
        _recent_results[(agent["id"], kind)].append(text)
    return reply, source, ver, prepared


@router.post("/assist/{kind}")
async def create_assist(kind: str, request: Request, body: AssistIn = Body(default_factory=AssistIn)) -> dict:
    reply, source, ver, prepared = await run_kind(kind, request, slug=body.slug, version=body.version,
                                                  input=body.input, avoid=body.avoid)
    if source == "share":
        return reply  # share-link visitors never see configuration or context details
    return {**reply, "kind": kind, "version": ver["n"] if ver else None, "diagnostics": prepared.diagnostics}


@router.post("/agents/{slug}/sample-task")
async def sample_task(slug: str, request: Request, body: SampleTaskIn = Body(default_factory=SampleTaskIn)) -> dict:
    """Alias of `POST /v1/assist/task` with a flat response {title, prompt, rationale}."""
    reply, source, ver, prepared = await run_kind("task", request, slug=slug, version=body.version,
                                                  input={"hint": body.hint} if body.hint else {}, avoid=body.avoid)
    out = {"title": reply["result"]["title"], "prompt": reply["result"]["prompt"], "rationale": reply["rationale"]}
    if source == "share":
        return out
    return {**out, "version": ver["n"] if ver else None, "variety": prepared.diagnostics.get("variety"),
            "context": {k: v for k, v in prepared.diagnostics.items() if k != "variety"}}
