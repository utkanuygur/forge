"""Tool registry: the one seam between the agent loop and non-builtin tool sources.

The loop asks the registry for extra ToolSpecs after `run_started`, routes any call whose name the registry
owns to `call`, and closes it before `run_finished`. Sources (HTTP tools, MCP servers, callable agents) never
raise into the loop: failures come back as `is_error` results or `error` events.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from forge.types import AgentVersion, Executor, ToolCall, ToolSpec

# notify(type, payload) emits a trace event through the loop (so it is sequenced and redacted).
Notify = Callable[[str, dict[str, Any]], Awaitable[None]]

TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]|_(?!_)){0,31}$")
RESERVED_PREFIXES = ("mcp__", "agent__")
MAX_OUTPUT = 10_000


@dataclass
class ToolOutcome:
    output: str
    is_error: bool = False
    extra: dict[str, Any] = field(default_factory=dict)  # merged into the tool_result payload


class ToolSource(Protocol):
    kind: str  # "http" | "mcp" | "agent"

    async def open(self, notify: Notify) -> None: ...

    def specs(self) -> list[ToolSpec]: ...

    async def call(self, name: str, args: dict[str, Any], deadline: float) -> ToolOutcome: ...

    async def close(self) -> None: ...


def truncate_output(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    return f"{text[:head]}\n[... {len(text) - head - tail} chars truncated ...]\n{text[-tail:]}"


BUILTIN_TOOLS = ("bash", "read_file", "write_file", "edit_file")  # forge/loop.py TOOL_SPECS


def builtin_names() -> set[str]:
    return set(BUILTIN_TOOLS)


class ToolRegistry:
    def __init__(self, sources: list[ToolSource]) -> None:
        self.sources = sources
        self._owner: dict[str, ToolSource] = {}
        self._specs: list[ToolSpec] = []
        self._opened = False

    async def open(self, notify: Notify) -> None:
        """Opens every source (concurrently). A source that fails is reported and left out."""
        self._opened = True

        async def open_one(src: ToolSource) -> None:
            try:
                await src.open(notify)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await notify("error", {"message": f"{src.kind} tools failed to load: {type(exc).__name__}: {exc}",
                                       "source": src.kind})

        await asyncio.gather(*(open_one(s) for s in self.sources))
        taken = builtin_names()
        for src in self.sources:
            try:
                specs = src.specs()
            except Exception:  # noqa: BLE001
                continue
            for spec in specs:
                if spec.name in taken:
                    await notify("error", {"message": f"tool name {spec.name!r} is already taken; skipped",
                                           "source": src.kind})
                    continue
                taken.add(spec.name)
                self._owner[spec.name] = src
                self._specs.append(spec)

    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    def has(self, name: str) -> bool:
        return name in self._owner

    async def call(self, call: ToolCall, deadline: float) -> ToolOutcome:
        src = self._owner.get(call.name)
        if src is None:
            return ToolOutcome(f"unknown tool {call.name!r}", True)
        if "_raw" in call.args:
            return ToolOutcome(f"arguments for {call.name} were not a valid JSON object: "
                               f"{str(call.args['_raw'])[:200]}", True, {"source": src.kind})
        if deadline - time.monotonic() <= 0:
            return ToolOutcome("the run's time limit was reached before this tool could run", True,
                               {"source": src.kind})
        try:
            out = await src.call(call.name, call.args, deadline)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  (never crash the run)
            out = ToolOutcome(f"{type(exc).__name__}: {exc}", True)
        out.output = truncate_output(out.output)
        out.extra = {"source": src.kind, **out.extra}
        return out

    async def close(self) -> None:
        for src in self.sources:
            try:
                await asyncio.wait_for(src.close(), 15)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass


def build_registry(
    version: AgentVersion,
    *,
    secrets: dict[str, str] | None = None,
    executor: Executor | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    manager: Any = None,
) -> ToolRegistry | None:
    """Registry for a version's HTTP tools, MCP servers and callable agents; None when it has none.

    Callable agents need the server's run manager (`manager`); without one (local CLI runs) they are not offered.
    """
    from forge.tools.agents import AgentsSource
    from forge.tools.http import HttpSource
    from forge.tools.mcp import McpSource

    sources: list[ToolSource] = []
    if version.http_tools:
        sources.append(HttpSource(version.http_tools, secrets=secrets or {}, network=version.network))
    if version.mcp_servers:
        sources.append(McpSource(version.mcp_servers, secrets=secrets or {}, executor=executor,
                                 network=version.network))
    if version.callable_agents and manager is not None and run_id and agent_id:
        sources.append(AgentsSource(version.callable_agents, run_id=run_id, agent_id=agent_id, manager=manager))
    return ToolRegistry(sources) if sources else None


# ---------------------------------------------------------------- config validation


def validate_tool_config(http_tools: list[Any], mcp_servers: list[Any], callable_agents: list[str],
                         own_slug: str | None = None) -> list[str]:
    """Problems with a version's tool configuration (empty when valid). Agent existence is checked by the caller."""
    from forge.tools.http import check_schema, placeholders

    errors: list[str] = []
    builtins = builtin_names()
    seen: set[str] = set()
    for t in http_tools:
        if not TOOL_NAME_RE.match(t.name):
            errors.append(f"HTTP tool name {t.name!r} must start with a letter and use letters, digits, - or _ "
                          "(at most 64 characters)")
        elif t.name in builtins or t.name.startswith(RESERVED_PREFIXES):
            errors.append(f"HTTP tool name {t.name!r} is reserved")
        elif t.name in seen:
            errors.append(f"HTTP tool name {t.name!r} is used twice")
        seen.add(t.name)
        if err := check_schema(t.parameters):
            errors.append(f"HTTP tool {t.name!r}: {err}")
        if not t.url.lower().startswith(("http://", "https://")):
            errors.append(f"HTTP tool {t.name!r}: URL must start with http:// or https://")
        props = set((t.parameters or {}).get("properties", {}) or {})
        for p in placeholders(t.url):
            if p not in props:
                errors.append(f"HTTP tool {t.name!r}: URL placeholder {{{p}}} is not a parameter")
        if not 0 < t.timeout <= 120:
            errors.append(f"HTTP tool {t.name!r}: timeout must be between 0 and 120 seconds")
    names: set[str] = set()
    for s in mcp_servers:
        if not SERVER_NAME_RE.match(s.name):
            errors.append(f"MCP server name {s.name!r} must use letters, digits, - or single _ "
                          "(at most 32 characters)")
        elif s.name in names:
            errors.append(f"MCP server name {s.name!r} is used twice")
        names.add(s.name)
        if s.transport in ("http", "sse") and not s.url.lower().startswith(("http://", "https://")):
            errors.append(f"MCP server {s.name!r}: URL must start with http:// or https://")
        if s.transport == "stdio" and not s.command.strip():
            errors.append(f"MCP server {s.name!r}: a command is required for stdio servers")
    for slug in callable_agents:
        if own_slug and slug == own_slug:
            errors.append("an agent cannot call itself")
    if len(set(callable_agents)) != len(callable_agents):
        errors.append("callable agents must not repeat")
    return errors
