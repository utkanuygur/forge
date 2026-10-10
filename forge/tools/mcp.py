"""MCP servers as tool sources, using the official MCP Python SDK.

Remote servers speak Streamable HTTP or SSE from the server process (network policy and SSRF checks apply).
Local stdio servers run inside the agent's sandbox container through `docker exec -i`, so they get the same
isolation, network policy and workspace as the agent's own commands. With the local executor they run on the
host, and in ask mode only after the user confirms.

Each connection lives in its own asyncio task that enters and exits the SDK's context managers, so the SDK's
anyio scopes never leak into the agent loop; tool calls are plain awaits on the open session.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
from contextlib import AsyncExitStack
from typing import Any

from forge import settings
from forge.tools.http import SECRET_RE, ToolError, check_destination, substitute_secrets
from forge.tools.registry import Notify, ToolOutcome
from forge.types import Executor, McpServer, ToolSpec

settings.register_setting(
    key="mcp_connect_timeout", env="FORGE_MCP_CONNECT_TIMEOUT", type="float", default=30, min=1, max=600,
    group="Tools", label="MCP connect timeout (seconds)",
    description="Seconds to wait when connecting to an MCP server and listing its tools.")


def connect_timeout() -> float:
    return float(settings.get("mcp_connect_timeout"))
MAX_TOOLS_PER_SERVER = 200
NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


def tool_name(server: str, tool: str) -> str:
    prefix = f"mcp__{server}__"
    return prefix + NAME_RE.sub("_", tool)[: max(1, 64 - len(prefix))]


def _leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def describe_error(exc: BaseException) -> str:
    leaf = _leaf(exc)
    msg = str(leaf) or type(leaf).__name__
    return msg if isinstance(leaf, ToolError) else f"{type(leaf).__name__}: {msg}"


def _field(obj: Any, *names: str) -> Any:
    for n in names:
        if isinstance(obj, dict):
            if n in obj:
                return obj[n]
        elif hasattr(obj, n):
            return getattr(obj, n)
    return None


def map_content(block: Any) -> str:
    """One MCP content block as text: text as is, images and audio summarized, resources as text if possible."""
    kind = _field(block, "type")
    if kind == "text":
        return str(_field(block, "text") or "")
    if kind == "image":
        return "[image]"
    if kind == "audio":
        return "[audio]"
    if kind == "resource":
        res = _field(block, "resource")
        text = _field(res, "text")
        if text is not None:
            return str(text)
        return f"[resource {_field(res, 'uri') or ''}]".replace(" ]", "]")
    if kind == "resource_link":
        return f"[resource link: {_field(block, 'uri') or ''}]"
    return f"[{kind or 'content'}]"


def map_result(result: Any) -> tuple[str, bool]:
    """(text, is_error) for a CallToolResult (SDK model or plain dict)."""
    content = _field(result, "content") or []
    parts = [map_content(b) for b in content]
    if not parts:
        structured = _field(result, "structured_content", "structuredContent")
        if structured is not None:
            parts.append(json.dumps(structured, indent=2, default=str))
    is_error = bool(_field(result, "is_error", "isError"))
    text = "\n".join(p for p in parts if p != "") or ("(the tool reported an error)" if is_error else "(no output)")
    return text, is_error


def input_schema(tool: Any) -> dict[str, Any]:
    schema = _field(tool, "input_schema", "inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    return schema


class McpConnection:
    """One connected MCP server. `start()` returns its tools; `call()` runs one; `close()` disconnects."""

    def __init__(self, server: McpServer, *, secrets: dict[str, str], executor: Executor | None,
                 network: bool) -> None:
        self.server = server
        self.secrets = secrets
        self.executor = executor
        self.network = network
        self.session: Any = None
        self.tools: list[Any] = []
        self._ready: asyncio.Future[list[Any]] | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._errlog: Any = None
        self.dead: str | None = None

    # -------------------------------------------------- transport

    def _subst(self, values: dict[str, str]) -> dict[str, str]:
        return {k: substitute_secrets(v, self.secrets) for k, v in values.items()}

    async def _transport(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        s = self.server
        if s.transport in ("http", "sse"):
            if not self.network:
                raise ToolError("network access is disabled for this agent; enable it to use remote MCP servers")
            await check_destination(s.url)
            headers = self._subst(s.headers)
            if s.transport == "http":
                import httpx2
                from mcp.client.streamable_http import streamable_http_client

                client = await stack.enter_async_context(
                    httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=300)))
                streams = await stack.enter_async_context(streamable_http_client(s.url, http_client=client))
            else:
                from mcp.client.sse import sse_client

                streams = await stack.enter_async_context(sse_client(s.url, headers=headers, timeout=30))
            return streams[0], streams[1]
        if s.transport == "stdio":
            from mcp import stdio_client

            params = await self._stdio_params()
            self._errlog = stack.enter_context(tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace"))
            streams = await stack.enter_async_context(stdio_client(params, errlog=self._errlog))
            return streams[0], streams[1]
        raise ToolError(f"unknown MCP transport {s.transport!r}")

    async def _stdio_params(self) -> Any:
        from mcp import StdioServerParameters

        s = self.server
        if any(SECRET_RE.search(a) for a in s.args) or SECRET_RE.search(s.command):
            raise ToolError("secrets can only be referenced in env values")
        env = self._subst(s.env)
        ex = self.executor
        container = getattr(ex, "container", None)
        if container:
            argv = ["exec", "-i", "-w", "/workspace"]
            for k, v in env.items():
                argv += ["-e", f"{k}={v}"]
            return StdioServerParameters(command="docker", args=[*argv, container, s.command, *s.args])
        if ex is not None and hasattr(ex, "_allowed") and hasattr(ex, "root"):  # LocalExecutor
            cmd = " ".join([s.command, *s.args])
            if not await ex._allowed(f"Start MCP server {s.name!r} on this machine in {ex.root}:\n{cmd}"):
                raise ToolError("the user declined starting this MCP server")
            return StdioServerParameters(command=s.command, args=list(s.args), env={**ex.env, **env}, cwd=str(ex.root))
        raise ToolError("stdio MCP servers need the docker or local executor")

    # -------------------------------------------------- lifecycle

    async def _main(self) -> None:
        from mcp import ClientSession

        assert self._ready is not None
        try:
            async with AsyncExitStack() as stack:
                read, write = await self._transport(stack)
                session = await stack.enter_async_context(
                    ClientSession(read, write, read_timeout_seconds=max(self.server.timeout, connect_timeout())))
                await session.initialize()
                tools: list[Any] = []
                cursor = None
                for _ in range(20):
                    from mcp.types import PaginatedRequestParams

                    page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
                    tools.extend(page.tools)
                    cursor = page.next_cursor
                    if not cursor or len(tools) >= MAX_TOOLS_PER_SERVER:
                        break
                self.session, self.tools = session, tools[:MAX_TOOLS_PER_SERVER]
                if not self._ready.done():
                    self._ready.set_result(self.tools)
                await self._stop.wait()
        except asyncio.CancelledError:
            if not self._ready.done():
                self._ready.set_exception(ToolError("connection cancelled"))
            raise
        except BaseException as exc:  # noqa: BLE001  (ExceptionGroups from anyio included)
            msg = describe_error(exc)
            if self._errlog is not None:
                msg = self._with_stderr(msg)
            self.dead = msg
            if not self._ready.done():
                self._ready.set_exception(ToolError(msg))
        finally:
            self.session = None
            if self.dead is None:
                self.dead = "the MCP server connection is closed"

    def _with_stderr(self, msg: str) -> str:
        try:
            self._errlog.seek(0)
            tail = self._errlog.read()[-600:].strip()
        except (OSError, ValueError):
            tail = ""
        return f"{msg}\nstderr: {tail}" if tail else msg

    async def start(self, timeout: float | None = None) -> list[Any]:
        timeout = connect_timeout() if timeout is None else timeout
        self._ready = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._main())
        try:
            return await asyncio.wait_for(asyncio.shield(self._ready), timeout)
        except TimeoutError:
            await self.close()
            raise ToolError(f"timed out after {timeout:.0f}s connecting") from None

    async def call(self, name: str, args: dict[str, Any], timeout: float) -> ToolOutcome:
        if self.session is None:
            return ToolOutcome(f"MCP server {self.server.name!r} is not connected: {self.dead or 'not started'}",
                               True)
        try:
            result = await asyncio.wait_for(self.session.call_tool(name, args, read_timeout_seconds=timeout),
                                            timeout + 2)
        except TimeoutError:
            return ToolOutcome(f"MCP tool {name!r} timed out after {timeout:.0f}s", True)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return ToolOutcome(f"MCP tool {name!r} failed: {describe_error(exc)}", True)
        text, is_error = map_result(result)
        return ToolOutcome(text, is_error)

    async def close(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), 10)
        except (TimeoutError, Exception):  # noqa: BLE001
            task.cancel()
            try:
                await asyncio.wait_for(task, 5)
            except BaseException:  # noqa: BLE001
                pass


class McpSource:
    kind = "mcp"

    def __init__(self, servers: list[McpServer], *, secrets: dict[str, str], executor: Executor | None,
                 network: bool) -> None:
        self.servers = servers
        self.secrets = secrets
        self.executor = executor
        self.network = network
        self.connections: list[McpConnection] = []
        self._routes: dict[str, tuple[McpConnection, str, dict[str, Any]]] = {}
        self._specs: list[ToolSpec] = []

    async def open(self, notify: Notify) -> None:
        async def connect(server: McpServer) -> None:
            conn = McpConnection(server, secrets=self.secrets, executor=self.executor, network=self.network)
            try:
                tools = await conn.start()
            except Exception as exc:  # noqa: BLE001
                await conn.close()
                await notify("error", {"message": f"MCP server {server.name!r} failed to start: "
                                                  f"{describe_error(exc)}", "source": "mcp", "server": server.name})
                return
            self.connections.append(conn)
            for t in tools:
                name = tool_name(server.name, t.name)
                if name in self._routes:
                    continue
                schema = input_schema(t)
                self._routes[name] = (conn, t.name, schema)
                desc = (t.description or t.title or t.name or "").strip()
                self._specs.append(ToolSpec(name=name, description=desc or f"{t.name} from {server.name}",
                                            parameters=schema))

        await asyncio.gather(*(connect(s) for s in self.servers))

    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    async def call(self, name: str, args: dict[str, Any], deadline: float) -> ToolOutcome:
        conn, tool, _schema = self._routes[name]
        timeout = min(max(1.0, conn.server.timeout), deadline - time.monotonic())
        if timeout <= 0:
            return ToolOutcome("the run's time limit was reached", True)
        out = await conn.call(tool, args, timeout)
        out.extra["mcp_server"] = conn.server.name
        return out

    async def close(self) -> None:
        await asyncio.gather(*(c.close() for c in self.connections), return_exceptions=True)
        self.connections = []


async def list_server_tools(server: McpServer, *, secrets: dict[str, str], executor: Executor | None,
                            network: bool) -> list[dict[str, Any]]:
    """Connects once and returns the tools as the agent would see them (builder "List tools")."""
    conn = McpConnection(server, secrets=secrets, executor=executor, network=network)
    try:
        tools = await conn.start()
        return [{"name": tool_name(server.name, t.name), "tool": t.name,
                 "description": (t.description or t.title or "").strip(), "input_schema": input_schema(t)}
                for t in tools]
    finally:
        await conn.close()
