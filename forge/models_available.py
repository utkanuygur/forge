"""Which models each configured provider offers right now (see CONTRACT.md "Settings").

Claude: GET {ANTHROPIC_BASE_URL or https://api.anthropic.com}/v1/models with the configured key. OpenAI compatible
endpoints (Ollama, vLLM, ...): GET {base_url}/models. Results are cached for 60 seconds per provider, and a provider
that is down or rejects the key is reported as unreachable; a lookup never raises.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from forge import settings

CACHE_TTL = 60.0
TIMEOUT = 4.0
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests swap in httpx.MockTransport

_cache: dict[tuple, tuple[float, dict]] = {}


def clear_cache() -> None:
    _cache.clear()


async def _get_json(url: str, headers: dict[str, str]) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT, transport=TRANSPORT) as client:
        r = await client.get(url, headers=headers)
    if r.status_code >= 400:
        try:
            body = r.json()
            err = body.get("error")
            detail = (err.get("message") if isinstance(err, dict) else err) or body.get("detail") or r.reason_phrase
        except Exception:  # noqa: BLE001
            detail = r.reason_phrase
        raise RuntimeError(f"HTTP {r.status_code}: {detail}")
    return r.json()


def _ids(body: Any) -> list[str]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        data = body.get("models") if isinstance(body, dict) else None  # Ollama's native /api/tags shape
    out = []
    for m in data or []:
        if isinstance(m, dict) and (mid := m.get("id") or m.get("name") or m.get("model")):
            out.append(str(mid))
    return sorted(set(out))


async def _cached(key: tuple, fetch: Any, refresh: bool) -> dict:
    hit = _cache.get(key)
    if hit and not refresh and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        models = await fetch()
        value = {"reachable": True, "models": models, "error": None}
    except Exception as exc:  # noqa: BLE001  (a provider being down must never fail the request)
        value = {"reachable": False, "models": [], "error": _plain_error(exc)}
    value["checked_at"] = time.time()
    _cache[key] = (time.monotonic(), value)
    return value


def _plain_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "connection refused"
    if isinstance(exc, httpx.TimeoutException):
        return f"no answer within {TIMEOUT:g} seconds"
    return str(exc) or type(exc).__name__


def anthropic_base() -> str:
    return (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")


async def anthropic_status(refresh: bool = False) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY") or ""
    out = {"id": "anthropic", "name": "Claude", "kind": "anthropic", "base_url": anthropic_base(),
           "prefix": "anthropic:", "configured": bool(key)}
    if not key:
        return {**out, "reachable": False, "models": [], "error": "no Claude API key is set", "checked_at": time.time()}

    async def fetch() -> list[str]:
        body = await _get_json(f"{anthropic_base()}/v1/models?limit=1000",
                               {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return _ids(body)

    # The cache key includes a digest of the key, so a changed key is checked again.
    return {**out, **await _cached(("anthropic", anthropic_base(), hash(key)), fetch, refresh)}


async def openai_status(base_url: str, name: str | None = None, refresh: bool = False) -> dict:
    base = base_url.rstrip("/")
    key = settings.openai_key_for(base)

    async def fetch() -> list[str]:
        return _ids(await _get_json(f"{base}/models", {"Authorization": f"Bearer {key}"} if key else {}))

    out = {"id": f"openai_compat:{base}", "name": name or base, "kind": "openai_compat", "base_url": base,
           "prefix": f"openai_compat:{base}|", "configured": True, "has_key": bool(key)}
    return {**out, **await _cached(("openai", base, hash(key)), fetch, refresh)}


async def providers(refresh: bool = False) -> list[dict]:
    """Every configured provider with reachability and model ids, checked concurrently."""
    tasks = [anthropic_status(refresh)] + [openai_status(e["base_url"], e["name"], refresh)
                                           for e in settings.endpoints()]
    return list(await asyncio.gather(*tasks))


async def model_warning(model: str | None) -> str | None:
    """A plain warning when `model` is not available on its provider right now, else None. Never raises."""
    if not model:
        return None
    kind, _, rest = model.partition(":")
    try:
        if kind == "anthropic":
            st = await anthropic_status()
            if not st["configured"]:
                return f"No Claude API key is set, so runs with '{rest}' will fail until one is added in Settings."
            if st["reachable"] and rest not in st["models"]:
                return f"Model '{rest}' is not available to this Claude API key; available: " + _list(st["models"])
            return None
        if kind == "openai_compat" and "|" in rest:
            base, _, name = rest.rpartition("|")
            st = await openai_status(base)
            if not st["reachable"]:
                return f"Could not reach {base.rstrip('/')} to check model '{name}' ({st['error']})."
            if name not in st["models"]:
                return f"Model '{name}' is not available on {base.rstrip('/')}; available: " + _list(st["models"])
    except Exception:  # noqa: BLE001
        return None
    return None


def _list(models: list[str], limit: int = 12) -> str:
    if not models:
        return "none"
    more = f" and {len(models) - limit} more" if len(models) > limit else ""
    return ", ".join(models[:limit]) + more
