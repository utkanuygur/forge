"""Request authentication: agent API keys, share tokens and the localhost builder bypass."""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request

from forge import db

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def is_local(request: Request) -> bool:
    if os.environ.get("FORGE_TRUST_ALL") == "1":
        return True
    return bool(request.client and request.client.host in LOCAL_HOSTS)


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() or None if h.lower().startswith("bearer ") else None


def _share(request: Request) -> str | None:
    return request.query_params.get("share") or request.headers.get("x-share-token")


def authorize(request: Request, agent: dict, *, allow_share: bool = True) -> str:
    """Returns the caller source ("api", "share" or "ui") or raises 401."""
    key = _bearer(request)
    if key and hmac.compare_digest(db.hash_key(key), agent["api_key_hash"]):
        return "api"
    share = _share(request)
    if allow_share and share and hmac.compare_digest(share, agent["share_token"]):
        return "share"
    if is_local(request):
        return "ui"
    raise HTTPException(401, "missing or invalid API key or share token")


def require_local(request: Request) -> None:
    """Builder routes that are not scoped to one agent."""
    if not is_local(request):
        raise HTTPException(401, "builder endpoints are only available from localhost")
