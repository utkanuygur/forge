"""Typed HTTP tools: JSON schema arguments, URL templates, secret headers, and SSRF protection.

Requests run from the server process (not the sandbox), so they are guarded here: the agent's version must
have `network` enabled, and every hop (including each redirect) must resolve only to public addresses unless
FORGE_ALLOW_PRIVATE_HTTP=1. The connection is pinned to the checked IP, so a DNS answer cannot change between
the check and the request.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import jsonschema

from forge.tools.registry import Notify, ToolOutcome
from forge.types import HttpTool, ToolSpec

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
SECRET_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
MAX_REDIRECTS = 5
MAX_TIMEOUT = 120.0
MAX_BODY_BYTES = 2_000_000
ERROR_EXCERPT = 2_000
BODY_METHODS = {"POST", "PUT", "PATCH"}


class ToolError(Exception):
    """A failure whose message goes back to the model as an is_error tool result."""


# ---------------------------------------------------------------- schema and arguments


def check_schema(schema: Any) -> str | None:
    """None when `schema` is a valid JSON schema for an object of arguments, else the problem."""
    if not isinstance(schema, dict):
        return "parameters must be a JSON schema object"
    if schema.get("type") != "object":
        return 'parameters must be a JSON schema with "type": "object"'
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        return f"invalid JSON schema: {exc.message}"
    return None


def validate_arguments(schema: dict[str, Any], args: dict[str, Any], tool: str) -> str | None:
    try:
        validator = jsonschema.Draft202012Validator(schema)
    except jsonschema.SchemaError as exc:
        return f"the schema of {tool} is invalid: {exc.message}"
    errors = sorted(validator.iter_errors(args), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    first = errors[0]
    where = "/".join(str(p) for p in first.absolute_path)
    return f"invalid arguments for {tool}: {first.message}" + (f" (at {where})" if where else "")


def placeholders(template: str) -> list[str]:
    return PLACEHOLDER_RE.findall(SECRET_RE.sub("", template))


def render_url(template: str, args: dict[str, Any]) -> tuple[str, set[str]]:
    """Fills {param} placeholders with URL-encoded argument values. Returns (url, names used)."""
    used: set[str] = set()

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in args or args[name] is None:
            raise ToolError(f"missing value for URL parameter {name!r}")
        used.add(name)
        value = args[name]
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, separators=(",", ":"))
        return quote(str(value), safe="")

    # {{SECRET}} references are not URL placeholders.
    if SECRET_RE.search(template):
        raise ToolError("secrets can only be referenced in headers")
    return PLACEHOLDER_RE.sub(sub, template), used


def substitute_secrets(value: str, secrets: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if not secrets.get(name):
            raise ToolError(f"secret {name} is not set for this agent")
        return secrets[name]

    return SECRET_RE.sub(sub, value)


def split_arguments(tool: HttpTool, args: dict[str, Any], used: set[str]) -> tuple[dict[str, Any], dict | None]:
    """(query parameters, JSON body or None) for the arguments not consumed by the URL template."""
    rest = {k: v for k, v in args.items() if k not in used and v is not None}
    forced = set(tool.query_params)
    if tool.method in BODY_METHODS:
        query = {k: v for k, v in rest.items() if k in forced}
        body = {k: v for k, v in rest.items() if k not in forced}
        return query, body
    return rest, None


def _query_value(v: Any) -> Any:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"))
    return v


# ---------------------------------------------------------------- SSRF protection


def private_allowed() -> bool:
    return os.environ.get("FORGE_ALLOW_PRIVATE_HTTP") == "1"


def is_blocked_ip(ip: str) -> bool:
    """True for loopback, private, link-local, multicast, reserved and unspecified addresses."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped or addr.sixtofour
        if mapped is not None:
            addr = mapped
        elif addr.teredo:
            addr = addr.teredo[1]
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified or not addr.is_global)


async def resolve(host: str, port: int) -> list[str]:
    """All IPs `host` resolves to (patched in tests)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def check_destination(url: str) -> tuple[str, str]:
    """Validates scheme and resolved addresses. Returns (host, ip to connect to); ip is "" when not pinned."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ToolError(f"only http and https URLs are allowed, not {parts.scheme or 'none'!r}")
    host = parts.hostname
    if not host:
        raise ToolError(f"URL has no host: {url}")
    if private_allowed():
        return host, ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        ips = await resolve(host, port)
    except (OSError, UnicodeError) as exc:
        raise ToolError(f"cannot resolve {host}: {exc}") from exc
    if not ips:
        raise ToolError(f"cannot resolve {host}")
    blocked = [ip for ip in ips if is_blocked_ip(ip)]
    if blocked:
        raise ToolError(f"blocked request to {host}: it resolves to a private, loopback or link-local address "
                        f"({blocked[0]}). Set FORGE_ALLOW_PRIVATE_HTTP=1 on the server to allow this.")
    return host, ips[0]


def _pinned(url: str, ip: str) -> tuple[httpx.URL, dict[str, str], dict[str, Any]]:
    """URL rewritten to connect to `ip`, plus the Host header and TLS SNI so the original name is still used."""
    u = httpx.URL(url)
    if not ip:
        return u, {}, {}
    host_header = u.host if u.port is None else f"{u.host}:{u.port}"
    ext = {"sni_hostname": u.host} if u.scheme == "https" else {}
    return u.copy_with(host=ip), {"Host": host_header}, ext


def _origin(url: str) -> tuple[str, str, int]:
    p = urlsplit(url)
    return p.scheme, (p.hostname or "").lower(), p.port or (443 if p.scheme == "https" else 80)


async def send_request(method: str, url: str, *, headers: dict[str, str], params: dict[str, Any],
                       json_body: Any, timeout: float,
                       transport: httpx.AsyncBaseTransport | None = None,
                       content: bytes | None = None) -> tuple[int, str, str, str]:
    """Sends the request, following up to MAX_REDIRECTS redirects with a fresh SSRF check on each hop.

    Returns (status, reason, content type, body text). Configured headers are dropped when a redirect leaves
    the original origin so secrets are never sent to another host.
    """
    current = str(httpx.URL(url, params={k: _query_value(v) for k, v in params.items()} or None))
    origin = _origin(current)
    hdrs = dict(headers)
    body = json_body
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout, transport=transport,
                                 trust_env=False) as client:
        for hop in range(MAX_REDIRECTS + 1):
            host, ip = await check_destination(current)
            target, extra_headers, ext = _pinned(current, ip)
            req = client.build_request(method, target, headers={**hdrs, **extra_headers},
                                       json=body if body is not None and content is None else None,
                                       content=content if method != "GET" else None, extensions=ext)
            resp = await client.send(req, stream=True)
            try:
                if resp.is_redirect and "location" in resp.headers:
                    if hop == MAX_REDIRECTS:
                        raise ToolError(f"too many redirects (more than {MAX_REDIRECTS})")
                    nxt = str(httpx.URL(current).join(resp.headers["location"]))
                    if resp.status_code == 303 or (resp.status_code in (301, 302) and method not in ("GET", "HEAD")):
                        method, body, content = "GET", None, None
                    if _origin(nxt) != origin:
                        hdrs = {}
                    current = nxt
                    continue
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_BODY_BYTES:
                        break
                raw = b"".join(chunks)[:MAX_BODY_BYTES]
                text = raw.decode(resp.encoding or "utf-8", errors="replace")
                return resp.status_code, resp.reason_phrase, resp.headers.get("content-type", ""), text
            finally:
                await resp.aclose()
    raise ToolError("too many redirects")  # pragma: no cover


# ---------------------------------------------------------------- running a tool


def _truncate(text: str, limit: int) -> str:
    limit = max(200, min(int(limit or 10_000), 100_000))
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[... {len(text) - limit} chars truncated ...]"


async def run_http_tool(tool: HttpTool, args: dict[str, Any], *, secrets: dict[str, str], network: bool,
                        deadline: float | None = None,
                        transport: httpx.AsyncBaseTransport | None = None) -> ToolOutcome:
    if not network:
        return ToolOutcome("network access is disabled for this agent; enable it to use HTTP tools", True)
    if err := validate_arguments(tool.parameters, args, tool.name):
        return ToolOutcome(err, True)
    timeout = max(1.0, min(float(tool.timeout or 30), MAX_TIMEOUT))
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            return ToolOutcome("the run's time limit was reached", True)
    try:
        url, used = render_url(tool.url, args)
        headers = {k: substitute_secrets(v, secrets) for k, v in tool.headers.items()}
        query, body = split_arguments(tool, args, used)
        status, reason, ctype, text = await asyncio.wait_for(
            send_request(tool.method, url, headers=headers, params=query, json_body=body, timeout=timeout,
                         transport=transport),
            timeout + 1)
    except ToolError as exc:
        return ToolOutcome(str(exc), True)
    except (TimeoutError, httpx.TimeoutException):
        return ToolOutcome(f"request timed out after {timeout:.0f}s", True)
    except httpx.HTTPError as exc:
        return ToolOutcome(f"request failed: {type(exc).__name__}: {exc}", True)
    head = f"HTTP {status} {reason}".strip()
    if not 200 <= status < 300:
        return ToolOutcome(f"{head}\n{_truncate(text, ERROR_EXCERPT)}", True, {"http_status": status})
    return ToolOutcome(f"{head}\n\n{_truncate(text, tool.max_response_chars)}", False, {"http_status": status})


class HttpSource:
    kind = "http"

    def __init__(self, tools: list[HttpTool], *, secrets: dict[str, str], network: bool,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.tools = {t.name: t for t in tools}
        self.secrets = secrets
        self.network = network
        self.transport = transport  # tests only

    async def open(self, notify: Notify) -> None:
        return None

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(name=t.name, description=t.description or t.name, parameters=t.parameters)
                for t in self.tools.values()]

    async def call(self, name: str, args: dict[str, Any], deadline: float) -> ToolOutcome:
        return await run_http_tool(self.tools[name], args, secrets=self.secrets, network=self.network,
                                   deadline=deadline, transport=self.transport)

    async def close(self) -> None:
        return None
