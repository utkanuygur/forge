"""POST /v1/agents/{slug}/sample-task: a realistic sample task for an agent, written by the grader model.

Builders (localhost or the agent's API key) and share-token callers may call it. Share callers are rate limited
in memory (per token, sliding hour) and only ever get {title, prompt, rationale}. See CONTRACT.md "Sample tasks".
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from forge import db, sample_task
from forge.server import evals
from forge.server.auth import authorize

log = logging.getLogger("forge.sample_task")
router = APIRouter(prefix="/v1")

SHARE_LIMIT_DEFAULT = 10
SHARE_WINDOW_S = 3600.0
KIND = "sample_task"  # grader_calls.kind


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
        return max(1, int(os.environ.get("FORGE_SAMPLE_TASK_SHARE_LIMIT") or SHARE_LIMIT_DEFAULT))
    except ValueError:
        return SHARE_LIMIT_DEFAULT


share_limiter = RateLimiter(_share_limit(), SHARE_WINDOW_S)


# ---------------------------------------------------------------- route


def _recent_inputs(agent_id: str) -> list[str]:
    return [r["input"] for r in db.list_runs(agent_id, limit=60)]


def _generation_count(agent_id: str) -> int:
    with db.connect() as c:
        row = c.execute("SELECT COUNT(*) FROM grader_calls WHERE agent_id = ? AND kind = ?", (agent_id, KIND)).fetchone()
    return int(row[0]) if row else 0


@router.post("/agents/{slug}/sample-task")
async def create_sample_task(slug: str, request: Request,
                             body: SampleTaskIn = Body(default_factory=SampleTaskIn)) -> dict:
    agent = db.get_agent_by_slug(slug)
    if agent is None:
        raise HTTPException(404, f"agent {slug!r} not found")
    source = authorize(request, agent)
    if source == "share":
        wait = share_limiter.check(agent["share_token"])
        if wait is not None:
            raise HTTPException(429, f"Too many sample tasks from this link. Try again in {math.ceil(wait / 60)} "
                                     "min.", headers={"Retry-After": str(math.ceil(wait))})
    n = body.version if body.version is not None and source != "share" else "prod"  # share links use prod
    version = db.get_version_n(agent["id"], n)
    if version is None:
        raise HTTPException(404, f"version {body.version} not found")

    ctx = sample_task.build_context(
        agent, version, recent_inputs=_recent_inputs(agent["id"]), avoid=body.avoid,
        secret_names=list(db.get_secrets(agent["id"])), generation=_generation_count(agent["id"]), hint=body.hint)
    grader = evals.MeteredProvider(sample_task.StructuredGrader(evals.grader(), sample_task.SAMPLE_TASK_SCHEMA),
                                   evals.grader_model(), agent["id"], KIND)
    try:
        task = await sample_task.generate_sample_task(ctx, grader)
    except sample_task.SampleTaskError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001  provider or network failure
        log.exception("sample task failed for %s", slug)
        raise HTTPException(502, f"The sample task model call failed: {type(exc).__name__}: {exc}") from exc

    out = {"title": task["title"], "prompt": task["prompt"], "rationale": task["rationale"]}
    if source == "share":
        return out  # share-link visitors never see configuration or context details
    return {**out, "version": version["n"], "variety": ctx["variety"],
            "context": {"files_listed": len(ctx["files"]), "total_files": ctx["total_files"],
                        "excerpts": [p for p, _ in ctx["excerpts"]],
                        "excerpt_chars": sum(len(e) for _, e in ctx["excerpts"]),
                        "system_prompt_chars": ctx["system_prompt_chars"],
                        "recent_inputs": len(ctx["recent_inputs"]), "avoid": len(ctx["avoid"])}}
