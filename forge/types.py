"""Shared types and interfaces. This module is the frozen contract between components.

Do not change signatures here without updating CONTRACT.md and every implementer.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- events

EventType = Literal["run_started", "assistant", "tool_call", "tool_result", "run_finished", "error",
                    # governance (additive)
                    "approval_requested", "approval_resolved", "budget_warning"]


class Event(BaseModel):
    """One step of a run's trace. `seq` is assigned by the runtime, starting at 0 per run."""

    run_id: str
    seq: int
    ts: float  # unix seconds
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    # Payload shapes by type:
    #   run_started:  {"version_id", "model", "input"}
    #   assistant:    {"text", "kind"}  kind: "progress" (narration before tool calls) | "final" (answer)
    #   tool_call:    {"id", "name", "args"}
    #   tool_result:  {"id", "name", "output", "is_error"}; non-builtin tools add "source"
    #                 ("http" | "mcp" | "agent") and agent tools add "child_run_id"
    #   run_finished: {"status", "output", "turns", "tokens_in", "tokens_out", "cache_read_tokens",
    #                  "cache_write_tokens"}
    #   error:        {"message"}
    # Additive observability fields (optional; absent on events recorded before they existed):
    #   assistant:    "turn", "duration_ms" (model call latency), "tokens_in", "tokens_out" (this turn)
    #   tool_call:    "turn"; the first call of a turn without text also has "model_duration_ms",
    #                 "tokens_in", "tokens_out"
    #   tool_result:  "duration_ms" (tool execution time, excluding any approval wait); when the call
    #                 waited on a human approval also "approval_wait_ms" (governance)
    #   error:        "duration_ms" (time until the model call failed), when raised by the model; for a
    #                 refused turn also "turn", "tokens_in", "tokens_out"
    #   per-turn usage also carries "cache_read_tokens" / "cache_write_tokens" when nonzero
    # Governance events:
    #   approval_requested: {"id", "tool_call_id", "name", "args", "rule", "expires_at"}
    #   approval_resolved:  {"id", "tool_call_id", "decision": "approve"|"deny", "note", "by"}
    #   budget_warning:     {"scope": "run"|"month", "limit_usd", "spent_usd", "fraction"}


Emit = Callable[[Event], Awaitable[None]]

# ---------------------------------------------------------------- agent config


class HttpTool(BaseModel):
    """A typed tool that calls an HTTP endpoint from the server process (see CONTRACT.md, Tools)."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema (type "object")
    url: str  # may contain {param} placeholders, filled URL-encoded from the arguments
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)  # values may reference secrets as {{NAME}}
    # Arguments not used in the URL go to the query string for GET and DELETE and to a JSON body otherwise;
    # names listed here always go to the query string.
    query_params: list[str] = Field(default_factory=list)
    timeout: float = 30.0  # seconds, capped at 120 and by the run's remaining wall clock
    max_response_chars: int = 10_000


class McpServer(BaseModel):
    """An MCP server whose tools are offered to the agent as `mcp__<name>__<tool>`."""

    name: str  # letters, digits, `-` and `_`; unique per version
    transport: Literal["http", "sse", "stdio"] = "http"
    url: str = ""  # http (Streamable HTTP) and sse
    headers: dict[str, str] = Field(default_factory=dict)  # http and sse; values may reference {{SECRET}}
    command: str = ""  # stdio: runs inside the agent's sandbox (`docker exec -i`)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)  # stdio; values may reference {{SECRET}}
    timeout: float = 60.0  # seconds per tool call


class AgentVersion(BaseModel):
    """Immutable snapshot of an agent's configuration."""

    id: str
    agent_id: str
    n: int
    system_prompt: str
    lessons: list[str] = Field(default_factory=list)
    model: str  # "anthropic:<model>" | "openai_compat:<base_url>|<model>"
    tools: list[str] = Field(default_factory=lambda: ["bash", "read_file", "write_file", "edit_file"])
    http_tools: list[HttpTool] = Field(default_factory=list)
    mcp_servers: list[McpServer] = Field(default_factory=list)
    callable_agents: list[str] = Field(default_factory=list)  # agent slugs offered as `agent__<slug>` tools
    network: bool = False
    seed_files: dict[str, str] = Field(default_factory=dict)  # relative path -> text content
    parent_id: str | None = None
    created_by: Literal["human", "optimizer"] = "human"
    rationale: str = ""
    created_at: float = 0.0
    # governance (additive): see forge/policy.py and forge/budget.py
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    max_run_cost_usd: float | None = None


# ---------------------------------------------------------------- model provider


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


class Message(BaseModel):
    """Provider-neutral conversation message.

    role="user": `text` is the user input.
    role="assistant": `text` and/or `tool_calls`.
    role="tool": `tool_call_id`, `text` is the tool output, `is_error` flags failures.
    """

    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    is_error: bool = False


class ModelResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # Short progress updates the model wrote between tool calls (e.g. Claude progress-update
    # thinking summaries), in order. Not replayed as text; the provider keeps the raw blocks.
    notes: list[str] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    # Why the model stopped, normalized across providers: "end_turn", "tool_use", "max_tokens", "refusal",
    # "stop_sequence", "pause_turn", ... (OpenAI-compatible finish_reason "length" maps to "max_tokens",
    # "content_filter" to "refusal"). None when the provider does not report it.
    stop_reason: str | None = None
    stop_detail: str | None = None  # refusal category or explanation when stop_reason == "refusal"
    cache_read_tokens: int = 0  # input tokens served from the prompt cache (Anthropic)
    cache_write_tokens: int = 0  # input tokens written to the prompt cache (Anthropic)


class Provider(Protocol):
    async def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        temperature: float | None = None,
    ) -> ModelResponse: ...


# ---------------------------------------------------------------- executor


class ExecResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Executor(Protocol):
    """Where tool calls run. All paths are relative to the workspace root."""

    async def start(self, seed_files: dict[str, bytes], env: dict[str, str]) -> None:
        """Create the workspace, write seed files, `git init` and commit them as the baseline."""

    async def exec(self, cmd: str, timeout: int = 120) -> ExecResult: ...

    async def read(self, path: str) -> str: ...

    async def write(self, path: str, content: str) -> None: ...

    async def list(self, path: str = ".") -> list[str]: ...

    async def export_workspace(self, archive_path: str, diff_path: str) -> None:
        """Write a .tar.gz of the workspace and a unified diff against the baseline commit."""

    async def stop(self) -> None:
        """Release all resources. Must be safe to call more than once."""


# ---------------------------------------------------------------- run result

RunStatus = Literal["queued", "running", "succeeded", "failed", "timeout", "cancelled",  # cancelled: additive
                    "awaiting_approval", "budget_exceeded"]  # governance (additive)


# Every status in one place (server, metrics, sessions and triggers use these sets).
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "timeout", "budget_exceeded", "cancelled"})
ACTIVE_STATUSES = frozenset({"queued", "running", "awaiting_approval"})  # awaiting_approval keeps its slot


class RunResult(BaseModel):
    status: RunStatus
    output: str = ""
    turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    error: str | None = None


# The runtime entry point (implemented in forge/loop.py):
#
#   async def run_agent(
#       version: AgentVersion,
#       input: str,
#       *,
#       run_id: str,
#       provider: Provider,
#       executor: Executor,          # already started by the caller
#       emit: Emit,
#       secrets: dict[str, str] | None = None,
#       temperature: float | None = None,
#       max_turns: int = 30,
#       max_seconds: int = 600,
#       hooks: RunHooks | None = None,   # governance (additive): forge.governance.RunGovernor
#       history: list[Message] | None = None,   # additive: earlier session turns
#   ) -> RunResult
#
# Factories (forge/providers.py, forge/executors.py):
#
#   def make_provider(model: str) -> Provider
#   def make_executor(kind: Literal["docker", "local"], *, network: bool = False,
#                     root: str | None = None, mode: Literal["ask", "auto"] = "auto",
#                     confirm: Callable[[str], bool | Awaitable[bool]] | None = None) -> Executor
