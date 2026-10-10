"""Assist kinds: each kind is one grader-written suggestion for a field a user would otherwise type by hand.

A kind registers how to build its request (`build_context`), the system prompt, the JSON schema of its `result`,
a validator, and whether share-link visitors may call it. The engine (forge.assist.engine) and the route
(forge.server.assist) are shared by every kind. See CONTRACT.md "Assist" for the rules and how to add a kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

_KIND_RE = r"^[a-z][a-z0-9_]{0,31}$"


@dataclass
class AssistRequest:
    """What the route resolved for one call. `agent`/`version` are None for kinds with `needs_agent=False`."""

    kind: str
    agent: dict | None = None
    version: dict | None = None
    input: dict[str, Any] = field(default_factory=dict)  # kind-specific, from the request body
    avoid: list[str] = field(default_factory=list)
    recent_inputs: list[str] = field(default_factory=list)  # newest first, all sources
    secret_names: list[str] = field(default_factory=list)  # names only, never values
    generation: int = 0  # earlier calls of this kind for this agent (for variety)
    recent_results: list[str] = field(default_factory=list)  # this kind's last results for the agent, newest first
    source: str = "ui"  # ui | api | share


@dataclass
class Prepared:
    """A kind's request to the model: the user message, data for its validator, and builder-only diagnostics."""

    text: str
    data: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssistKind:
    kind: str
    build_context: Callable[[AssistRequest], Prepared]
    system_prompt: str
    schema: dict[str, Any]  # JSON schema of `result` (object, additionalProperties false, all keys required)
    validate: Callable[[dict[str, Any], dict[str, Any]], list[str]]  # (result, prepared.data) -> problems
    share_allowed: bool = False
    needs_agent: bool = True
    fake: Callable[[str], dict[str, Any]] | None = None  # fake:grader reply {result, rationale} given the request
    # The text a result contributes to later `avoid` lists (the server remembers recent results per agent and kind).
    result_text: Callable[[dict[str, Any]], str] | None = None


_KINDS: dict[str, AssistKind] = {}


def register(kind: str, build_context: Callable[[AssistRequest], Prepared], system_prompt: str,
             schema: dict[str, Any], validate: Callable[[dict[str, Any], dict[str, Any]], list[str]], *,
             share_allowed: bool = False, needs_agent: bool = True,
             fake: Callable[[str], dict[str, Any]] | None = None,
             result_text: Callable[[dict[str, Any]], str] | None = None) -> AssistKind:
    import re

    if not re.match(_KIND_RE, kind):
        raise ValueError(f"assist kind {kind!r} must be lowercase letters, digits and underscores")
    if kind in _KINDS:
        raise ValueError(f"assist kind {kind!r} is already registered")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("an assist schema must be an object with additionalProperties false")
    if any(k.system_prompt == system_prompt for k in _KINDS.values()):
        raise ValueError("each assist kind needs its own system prompt")
    spec = AssistKind(kind, build_context, system_prompt, schema, validate, share_allowed, needs_agent, fake,
                      result_text)
    _KINDS[kind] = spec
    return spec


def unregister(kind: str) -> None:
    """For tests."""
    _KINDS.pop(kind, None)


def get(kind: str) -> AssistKind | None:
    return _KINDS.get(kind)


def kinds() -> list[str]:
    return sorted(_KINDS)


def by_system_prompt(system: str) -> AssistKind | None:
    """Used by `fake:grader` to recognize an assist call."""
    return next((k for k in _KINDS.values() if k.system_prompt == system), None)


def envelope_schema(spec: AssistKind) -> dict[str, Any]:
    """The structured output schema of every assist reply: the kind's result plus a one sentence rationale."""
    return {"type": "object", "properties": {"result": spec.schema, "rationale": {"type": "string"}},
            "required": ["result", "rationale"], "additionalProperties": False}
