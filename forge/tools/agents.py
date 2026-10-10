"""Agents calling agents: each allowed agent is offered as `agent__<slug>` and runs as a child run.

A call starts a child run of the callee's production version through the run manager (source `agent`,
`parent_run_id` set), waits for it within the parent's remaining wall clock, and returns its final output.
Calls are limited to MAX_DEPTH levels below the top run, and an agent can never call itself or an agent that
is already in its call chain.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from forge import db
from forge.tools.registry import Notify, ToolOutcome
from forge.types import ToolSpec

MAX_DEPTH = 3
MIN_SECONDS = 5  # a child needs at least this much of the parent's remaining wall clock
MARGIN = 2  # seconds kept back so the parent can still use the child's answer


def agent_tool_name(slug: str) -> str:
    return f"agent__{slug}"


def check_call(run_id: str, target_agent_id: str, target_slug: str) -> str | None:
    """Depth and cycle check for a call from run `run_id` to agent `target_agent_id`. None when allowed."""
    chain = db.run_ancestry(run_id)
    depth = len(chain) - 1  # 0 for a top level run
    if depth + 1 > MAX_DEPTH:
        return f"agent call depth limit of {MAX_DEPTH} reached; {target_slug} cannot be called from here"
    agent_ids = [r["agent_id"] for r in chain]
    if target_agent_id in agent_ids:
        slugs = []
        for aid in reversed(agent_ids):
            a = db.get_agent(aid)
            slugs.append(a["slug"] if a else aid)
        path = " > ".join([*slugs, target_slug])
        return f"cycle detected: {target_slug} is already in this call chain ({path})"
    return None


class AgentsSource:
    kind = "agent"

    def __init__(self, slugs: list[str], *, run_id: str, agent_id: str, manager: Any) -> None:
        self.slugs = slugs
        self.run_id = run_id
        self.agent_id = agent_id
        self.manager = manager
        self._agents: dict[str, dict] = {}
        self._specs: list[ToolSpec] = []

    async def open(self, notify: Notify) -> None:
        for slug in self.slugs:
            agent = db.get_agent_by_slug(slug)
            if agent is None:
                await notify("error", {"message": f"callable agent {slug!r} not found; its tool is not offered",
                                       "source": "agent"})
                continue
            if agent["id"] == self.agent_id:
                continue
            name = agent_tool_name(slug)
            self._agents[name] = agent
            about = (agent["description"] or "").strip() or f"The {agent['name']} agent."
            self._specs.append(ToolSpec(
                name=name,
                description=f"{about} Delegates a task to the {agent['name']} agent and returns its final answer.",
                parameters={"type": "object", "properties": {
                    "input": {"type": "string", "description": f"The task or question for {agent['name']}."}},
                    "required": ["input"]},
            ))

    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    async def call(self, name: str, args: dict[str, Any], deadline: float) -> ToolOutcome:
        agent = self._agents[name]
        text = args.get("input")
        if not isinstance(text, str) or not text.strip():
            return ToolOutcome(f"argument 'input' of {name} must be a non-empty string", True)
        if err := check_call(self.run_id, agent["id"], agent["slug"]):
            return ToolOutcome(err, True)
        remaining = deadline - time.monotonic() - MARGIN
        if remaining < MIN_SECONDS:
            return ToolOutcome("not enough time left in this run to call another agent", True)
        version = db.get_version(agent["prod_version_id"])
        if version is None:
            return ToolOutcome(f"agent {agent['slug']} has no production version", True)
        child_id = await self.manager.start_run(agent, version, text, "agent", parent_run_id=self.run_id,
                                                max_seconds=int(remaining))
        extra = {"child_run_id": child_id, "child_agent": agent["slug"]}
        try:
            await asyncio.wait_for(self.manager.wait(child_id), remaining + 1)
        except TimeoutError:
            return ToolOutcome(f"{agent['slug']} did not finish within this run's time limit (child run {child_id})",
                               True, extra)
        child = db.get_run(child_id) or {}
        status = child.get("status", "failed")
        output = child.get("output") or ""
        if status != "succeeded" and child.get("error"):
            output = f"{output}\n{child['error']}".strip()
        note = f"[{agent['slug']} {status} in child run {child_id}]"
        return ToolOutcome(f"{output}\n\n{note}" if output else note, status != "succeeded",
                           {**extra, "child_status": status})

    async def close(self) -> None:
        return None
