"""In-process client for Forge's own HTTP API: every assistant tool goes through the real endpoints.

Requests are sent to the ASGI app directly (no network) with the caller's credentials: a localhost builder stays
a localhost builder, and an API-key caller keeps only that key's rights (its agent), so the assistant can never do
more than the person using it. Errors come back as ToolError with the endpoint's `detail`.
"""

from __future__ import annotations

from typing import Any

import httpx

LOCAL_CLIENT = ("127.0.0.1", 0)
# Any non-loopback address: auth.is_local() then refuses the localhost bypass, so only the forwarded key counts.
REMOTE_CLIENT = ("192.0.2.1", 0)


class ToolError(Exception):
    """A tool failed in a way the model should see and recover from (bad arguments, 4xx from the API...)."""


class ForgeApi:
    def __init__(self, *, local: bool, authorization: str | None = None, app: Any = None) -> None:
        self.local, self.authorization, self._app = local, authorization, app

    def _client(self) -> httpx.AsyncClient:
        app = self._app
        if app is None:
            from forge.server.app import app  # lazy: the app imports this package's router
        transport = httpx.ASGITransport(app=app, client=LOCAL_CLIENT if self.local else REMOTE_CLIENT)
        headers = {"Authorization": self.authorization} if self.authorization and not self.local else {}
        return httpx.AsyncClient(transport=transport, base_url="http://forge.internal/v1", headers=headers,
                                 timeout=60)

    async def call(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                   with_total: bool = False) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        async with self._client() as c:
            r = await c.request(method, path, json=json, params=params)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:300]
            if not isinstance(detail, str):
                detail = str(detail)[:500]
            raise ToolError(f"{r.status_code}: {detail}")
        body = r.json() if "json" in r.headers.get("content-type", "") else r.text
        if with_total:
            total = r.headers.get("x-total-count")
            return body, int(total) if total and total.isdigit() else None
        return body

    async def get(self, path: str, **params: Any) -> Any:
        return await self.call("GET", path, params=params)

    async def post(self, path: str, json: Any = None) -> Any:
        return await self.call("POST", path, json=json if json is not None else {})
