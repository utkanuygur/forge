"""The agent loop: model <-> tools until the model answers without tool calls."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from forge.types import (
    AgentVersion,
    Emit,
    Event,
    EventType,
    Executor,
    Message,
    Provider,
    RunResult,
    ToolCall,
    ToolSpec,
)

MAX_OUTPUT = 10_000
KEEP_HEAD = 4_000
KEEP_TAIL = 6_000
MAX_BASH_TIMEOUT = 120
REDACTED = "[REDACTED]"

TOOL_SPECS: dict[str, ToolSpec] = {
    "bash": ToolSpec(
        name="bash",
        description=(
            "Run a shell command with bash in the workspace root and return its exit code, stdout and stderr. "
            "Use it to inspect files (ls, rg, cat), run tests and builds, and use git. Each call is a fresh shell: "
            "`cd` does not persist between calls. Commands are killed after `timeout` seconds. "
            "Output longer than 10,000 characters is truncated in the middle."
        ),
        parameters={
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "The bash command to run."},
                "timeout": {
                    "type": "integer",
                    "description": f"Seconds before the command is killed (1 to {MAX_BASH_TIMEOUT}, default {MAX_BASH_TIMEOUT}).",
                },
            },
            "required": ["cmd"],
        },
    ),
    "read_file": ToolSpec(
        name="read_file",
        description=(
            "Read a text file and return its lines prefixed with 1-based line numbers. Optionally read only the "
            "inclusive line range start..end. Paths are relative to the workspace root."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root."},
                "start": {"type": "integer", "description": "First line to return (1-based, default 1)."},
                "end": {"type": "integer", "description": "Last line to return (inclusive, default end of file)."},
            },
            "required": ["path"],
        },
    ),
    "write_file": ToolSpec(
        name="write_file",
        description=(
            "Create or overwrite a file with the given content. Parent directories are created as needed. "
            "Prefer edit_file for small changes to existing files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root."},
                "content": {"type": "string", "description": "The full new content of the file."},
            },
            "required": ["path", "content"],
        },
    ),
    "edit_file": ToolSpec(
        name="edit_file",
        description=(
            "Replace one exact occurrence of `old` with `new` in a file. `old` must match the file exactly, "
            "including whitespace and indentation, and must occur exactly once; include surrounding lines to make "
            "it unique. Read the file first if unsure of its exact contents."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root."},
                "old": {"type": "string", "description": "Exact text to replace (must occur exactly once)."},
                "new": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old", "new"],
        },
    ),
}

_TYPES: dict[str, tuple[type, ...]] = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,)}


def build_system_prompt(version: AgentVersion) -> str:
    parts = [version.system_prompt.strip()]
    if version.lessons:
        parts.append("## Lessons learned\n" + "\n".join(f"- {lesson}" for lesson in version.lessons))
    net = "enabled" if version.network else "disabled (commands cannot reach the internet)"
    parts.append(
        "## Environment\n"
        "- Your working directory is the workspace root (/workspace). All file paths are relative to it.\n"
        f"- Network access is {net}.\n"
        "- When you are done, reply without calling any tools and summarize what you did."
    )
    return "\n\n".join(p for p in parts if p)


def truncate(text: str, limit: int = MAX_OUTPUT, head: int = KEEP_HEAD, tail: int = KEEP_TAIL) -> str:
    if len(text) <= limit:
        return text
    cut = len(text) - head - tail
    return f"{text[:head]}\n[... {cut} chars truncated ...]\n{text[-tail:]}"


def redact(value: Any, secrets: list[str]) -> Any:
    if not secrets:
        return value
    if isinstance(value, str):
        for s in secrets:
            value = value.replace(s, REDACTED)
        return value
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    return value


def validate_args(spec: ToolSpec, args: dict[str, Any]) -> str | None:
    """Minimal JSON-schema check (required keys and primitive types). Returns an error message or None."""
    if "_raw" in args:
        return f"arguments for {spec.name} were not a valid JSON object: {str(args['_raw'])[:200]}"
    props = spec.parameters.get("properties", {})
    missing = [k for k in spec.parameters.get("required", []) if k not in args]
    if missing:
        return f"missing required argument(s) for {spec.name}: {', '.join(missing)}"
    for k, v in args.items():
        want = props.get(k, {}).get("type")
        if want and v is not None and (not isinstance(v, _TYPES[want]) or (want != "boolean" and isinstance(v, bool))):
            return f"argument {k!r} of {spec.name} must be of type {want}"
    return None


async def _run_tool(call: ToolCall, executor: Executor, allowed: set[str], deadline: float) -> tuple[str, bool]:
    if call.name not in allowed or call.name not in TOOL_SPECS:
        return f"unknown tool {call.name!r}; available tools: {', '.join(sorted(allowed))}", True
    if err := validate_args(TOOL_SPECS[call.name], call.args):
        return err, True
    a = call.args
    try:
        if call.name == "bash":
            limit = max(1, min(int(a.get("timeout") or MAX_BASH_TIMEOUT), MAX_BASH_TIMEOUT))
            limit = max(1, min(limit, int(deadline - time.monotonic())))
            r = await executor.exec(a["cmd"], timeout=limit)
            out = f"exit_code: {r.exit_code}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
            if r.timed_out:
                out += f"\n[command timed out after {limit}s and was killed]"
            return truncate(out), r.exit_code != 0
        if call.name == "read_file":
            lines = (await executor.read(a["path"])).splitlines()
            start = max(1, a.get("start") or 1)
            end = min(len(lines), a.get("end") or len(lines))
            if not lines:
                return "(empty file)", False
            if start > end:
                return f"empty range {start}..{end}; the file has {len(lines)} lines", True
            width = len(str(end))
            return truncate("\n".join(f"{i:>{width}}\t{lines[i - 1]}" for i in range(start, end + 1))), False
        if call.name == "write_file":
            await executor.write(a["path"], a["content"])
            return f"wrote {len(a['content'])} chars to {a['path']}", False
        if call.name == "edit_file":
            if a["old"] == "":
                return "`old` must not be empty; use write_file to create a file", True
            text = await executor.read(a["path"])
            n = text.count(a["old"])
            if n == 0:
                return f"`old` text not found in {a['path']}; read the file and copy the exact text", True
            if n > 1:
                return f"`old` text found {n} times in {a['path']}; add more context so it matches exactly once", True
            await executor.write(a["path"], text.replace(a["old"], a["new"], 1))
            return f"edited {a['path']}", False
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001  (tool failures go back to the model, never crash the run)
        return truncate(f"{type(exc).__name__}: {exc}"), True
    return f"tool {call.name} is not implemented", True


async def run_agent(
    version: AgentVersion,
    input: str,
    *,
    run_id: str,
    provider: Provider,
    executor: Executor,
    emit: Emit,
    secrets: dict[str, str] | None = None,
    temperature: float | None = None,
    max_turns: int = 30,
    max_seconds: int = 600,
    hooks: Any = None,
) -> RunResult:
    """`hooks` (optional, see forge.governance.RunGovernor) may stop the run after a model turn
    (`after_turn`) and replace a tool call's result (`before_tool`); time spent there, such as waiting
    for a human approval, does not count toward `max_seconds`."""
    # Longest first so a secret containing another secret is fully redacted.
    secret_values = sorted({v for v in (secrets or {}).values() if v}, key=len, reverse=True)
    seq = 0

    async def send(type_: EventType, payload: dict[str, Any]) -> None:
        nonlocal seq
        event = Event(run_id=run_id, seq=seq, ts=time.time(), type=type_, payload=redact(payload, secret_values))
        seq += 1
        await emit(event)

    deadline = time.monotonic() + max_seconds
    system = build_system_prompt(version)
    allowed = [t for t in version.tools if t in TOOL_SPECS]
    tools = [TOOL_SPECS[t] for t in allowed]
    messages: list[Message] = [Message(role="user", text=input)]
    result = RunResult(status="running")
    last_text = ""

    await send("run_started", {"version_id": version.id, "model": version.model, "input": input})
    try:
        while True:
            if result.turns >= max_turns:
                result.status, result.error = "timeout", f"turn limit of {max_turns} reached"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result.status, result.error = "timeout", f"time limit of {max_seconds}s reached"
                break
            try:
                resp = await asyncio.wait_for(provider.complete(system, messages, tools, temperature), remaining)
            except TimeoutError:
                result.status, result.error = "timeout", f"time limit of {max_seconds}s reached"
                break
            except Exception as exc:  # noqa: BLE001
                result.status, result.error = "failed", f"model error: {type(exc).__name__}: {exc}"
                await send("error", {"message": result.error})
                break
            result.turns += 1
            result.tokens_in += resp.tokens_in
            result.tokens_out += resp.tokens_out
            if resp.text:
                last_text = resp.text
                await send("assistant", {"text": resp.text})
            messages.append(Message(role="assistant", text=resp.text, tool_calls=resp.tool_calls))
            if hooks is not None and (stop := await hooks.after_turn(result, send)):
                result.status, result.error = stop
                await send("error", {"message": result.error})
                break
            if not resp.tool_calls:
                result.status, result.output = "succeeded", resp.text
                break
            for call in resp.tool_calls:
                await send("tool_call", {"id": call.id, "name": call.name, "args": call.args})
                gated = None
                if hooks is not None:
                    paused = time.monotonic()
                    gated = await hooks.before_tool(call, send)
                    deadline += time.monotonic() - paused
                if gated is not None:
                    output, is_error = gated
                else:
                    output, is_error = await _run_tool(call, executor, set(allowed), deadline)
                await send("tool_result", {"id": call.id, "name": call.name, "output": output, "is_error": is_error})
                messages.append(Message(role="tool", tool_call_id=call.id, text=output, is_error=is_error))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001  (unexpected runtime bug: report it, still finish the run)
        result.status, result.error = "failed", f"runtime error: {type(exc).__name__}: {exc}"
        await send("error", {"message": result.error})

    if result.status != "succeeded":
        result.output = last_text
    result.output = redact(result.output, secret_values)
    if result.error:
        result.error = redact(result.error, secret_values)
    await send(
        "run_finished",
        {
            "status": result.status,
            "output": result.output,
            "turns": result.turns,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
        },
    )
    return result
