"""Builder only settings endpoints (see CONTRACT.md "Settings").

GET   /v1/settings            every setting by group, with value, source, apply mode and needs_restart
PATCH /v1/settings            {key: value, ...}; 422 with per key errors, nothing saved when any key is invalid
POST  /v1/settings/reset      {"keys": [...]}: forget saved values, so the default (or environment) applies
GET   /v1/models/available    each configured provider: reachable or not and its model ids (cached 60 s)

Restarting is done by the desktop shell (an IPC call from the page to the Electron main process, which restarts
the server it owns); the dev server is restarted by hand. See CONTRACT.md.
"""

from __future__ import annotations

import asyncio
from importlib import metadata
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from forge import db, models_available, settings
from forge.server.auth import require_local

router = APIRouter(prefix="/v1", dependencies=[Depends(require_local)])


def forge_version() -> str:
    try:
        return metadata.version("forge")
    except metadata.PackageNotFoundError:
        return "dev"


def info() -> dict[str, Any]:
    """Read only facts about this server shown at the top of the Settings page."""
    from forge.server.observability import check_docker

    executor = settings.get("executor")
    return {
        "data_dir": str(db.data_dir()),
        "version": forge_version(),
        "executor": executor,
        "docker": check_docker() if executor == "docker" else {"status": "skipped", "detail": "executor is local"},
    }


async def payload() -> dict[str, Any]:
    groups = settings.groups()
    return {
        "desktop": settings.desktop(),
        "needs_restart": any(s["needs_restart"] for g in groups for s in g["settings"]),
        "groups": groups,
        "info": await asyncio.to_thread(info),
    }


@router.get("/settings")
async def get_settings() -> dict:
    return await payload()


@router.patch("/settings")
async def patch_settings(body: dict[str, Any] = Body(...)) -> dict:
    to_reset = [k for k, v in body.items() if v is None and k in settings.REGISTRY]
    values, errors = settings.validate_update({k: v for k, v in body.items() if k not in to_reset})
    for k in to_reset:  # null means "back to the default": still refused for secrets and environment values
        _, err = settings.validate_update({k: settings.REGISTRY[k].default})
        errors.update(err)
    if errors:
        raise HTTPException(422, {"message": "Some settings were not saved: " +
                                  "; ".join(f"{k} {m}" for k, m in errors.items()), "errors": errors})
    settings.save(values)
    settings.reset(to_reset)
    return await payload()


class ResetIn(BaseModel):
    keys: list[str]


@router.post("/settings/reset")
async def reset_settings(body: ResetIn) -> dict:
    unknown = [k for k in body.keys if k not in settings.REGISTRY]
    if unknown:
        raise HTTPException(422, {"message": f"unknown settings: {', '.join(unknown)}",
                                  "errors": {k: "unknown setting" for k in unknown}})
    settings.reset(body.keys)
    return await payload()


@router.get("/models/available")
async def available(refresh: bool = False) -> dict:
    return {"providers": await models_available.providers(refresh=refresh)}
