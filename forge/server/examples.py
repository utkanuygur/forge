"""Example agent endpoints (CONTRACT.md "Examples"): list the shipped examples and install them in one click.

Builder only: every route requires a localhost caller (the same rule as POST /v1/agents). The logic lives in
forge.examples; this module maps it to HTTP.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from forge import examples
from forge.server import evals
from forge.server.auth import require_local
from forge.server.views import agent_view

router = APIRouter(prefix="/v1", dependencies=[Depends(require_local)])


class InstallIn(BaseModel):
    model: str | None = Field(default=None, max_length=500)


def _model(body: InstallIn) -> str | None:
    return body.model.strip() if body.model and body.model.strip() else None


def _install(ex: dict[str, Any], model: str | None, replace: bool, promote: bool) -> dict[str, Any]:
    res = examples.install(ex, default_model=evals.default_model(), model=model, replace=replace, promote=promote)
    out = {**agent_view(res["agent"], versions=True), "example": ex["key"], "created": res["created"],
           "installed_version": res["version"], "eval_cases_synced": res["cases"]}
    if res["api_key"]:
        out["api_key"] = res["api_key"]
    return out


@router.get("/examples")
async def list_examples() -> list[dict]:
    return [examples.summary(ex) for ex in await asyncio.to_thread(examples.discover)]


@router.post("/examples/install-all")
async def install_all(body: InstallIn = Body(default_factory=InstallIn), replace: bool = False,
                      promote: bool = False) -> dict:
    """Installs every example that is not installed yet (with ?replace=1, also re-syncs the installed ones)."""
    installed, skipped = [], []
    for ex in await asyncio.to_thread(examples.discover):
        try:
            installed.append(await asyncio.to_thread(_install, ex, _model(body), replace, promote))
        except FileExistsError:
            skipped.append(ex["spec"]["slug"])
    return {"installed": installed, "skipped": skipped}


@router.post("/examples/{key}/install")
async def install_one(key: str, body: InstallIn = Body(default_factory=InstallIn), replace: bool = False,
                      promote: bool = False) -> dict:
    try:
        ex = await asyncio.to_thread(examples.get, key)
    except examples.ExampleError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        return await asyncio.to_thread(_install, ex, _model(body), replace, promote)
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
