"""Assist kind "approval_rules": suggested approval policy rules for the agent's tools and network setting.

Input (all optional; defaults come from the version): {"tools": [str], "network": bool, "http_tools": [str]
(names), "current": {"rules": [...]}}. Result: {"rules": [{"tool", "pattern", "mode": "ask"|"deny", "label"}],
"explanation": str}; `pattern` "" means every call. Rules already in `current` are never suggested again.
"""

from __future__ import annotations

import json
from typing import Any

from forge import policy as P
from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register

KIND = "approval_rules"
MAX_RULES = 8

SYSTEM = f"""You suggest approval rules for an AI agent: which tool calls must wait for a human decision \
("ask") or are refused outright ("deny"). The agent runs in an isolated sandbox with the tools listed in the \
request.

Rule format: {{"tool", "pattern", "mode", "label"}}.
- tool: a builtin tool name (bash, read_file, write_file, edit_file), one of the agent's HTTP tools, an MCP \
tool `mcp__<server>__<tool>` or `mcp__<server>__*`, an agent tool `agent__<slug>`, or "*" (any tool, then \
pattern must be ""). Only name tools the agent actually has.
- pattern: "" matches every call. For bash a Python regular expression searched in the command (use \\b word \
boundaries). For the file tools a path glob (`*` within a directory, `**` across directories; a glob without a \
slash matches the file name anywhere, e.g. `*.env`). For other tools a regular expression searched in the \
arguments as compact JSON.
- mode: "ask" for actions a human should confirm (pushing, deleting, spending money, sending messages, \
installing packages when network is on); "deny" only for actions the agent must never take.
- label: 1 to 4 words shown to the approver.

Suggest 1 to {MAX_RULES} rules that fit THIS agent's purpose, tools and network setting: protect what is risky \
for its work without blocking its normal job (a coding agent must still edit source files and run tests). Never \
repeat a rule already in the current policy. Commonly useful rules (adapt, do not copy blindly):
{json.dumps([r for rules in P.PRESETS.values() for r in rules], indent=0)}

Explain the set in one or two sentences in result.explanation. Respond with the JSON object only: \
{{"result": {{"rules": [{{"tool": "...", "pattern": "...", "mode": "ask", "label": "..."}}], "explanation": "..."}}, \
"rationale": "..."}}"""

RULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"tool": {"type": "string"}, "pattern": {"type": "string"},
                   "mode": {"type": "string", "enum": ["ask", "deny"]}, "label": {"type": "string"}},
    "required": ["tool", "pattern", "mode", "label"],
    "additionalProperties": False,
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"rules": {"type": "array", "items": RULE_SCHEMA}, "explanation": {"type": "string"}},
    "required": ["rules", "explanation"],
    "additionalProperties": False,
}


def _key(r: dict) -> tuple[str, str, str]:
    return (str(r.get("tool") or ""), str(r.get("pattern") or "").strip(), str(r.get("mode") or "ask"))


def build_context(req: AssistRequest) -> Prepared:
    assert req.version is not None
    inp = req.input
    tools = [t for t in inp.get("tools", req.version.get("tools") or []) if isinstance(t, str)]
    network = bool(inp.get("network", req.version.get("network")))
    http = [t for t in inp.get("http_tools", [h.get("name") for h in req.version.get("http_tools") or []
                                              if isinstance(h, dict)]) if isinstance(t, str)]
    mcp = [s.get("name") for s in req.version.get("mcp_servers") or [] if isinstance(s, dict)]
    agents = list(req.version.get("callable_agents") or [])
    current = inp.get("current") if isinstance(inp.get("current"), dict) else (req.version.get("approval_policy") or {})
    current_rules = [r for r in current.get("rules") or [] if isinstance(r, dict)]
    ctx, rendered = C.for_request(req)
    env = [f"builtin tools: {', '.join(tools) or '(none)'}", f"network: {'on' if network else 'off'}",
           f"http tools: {', '.join(http) or '(none)'}", f"mcp servers: {', '.join(mcp) or '(none)'}",
           f"callable agents: {', '.join(agents) or '(none)'}"]
    parts = [rendered, "<tool_environment>\n" + "\n".join(env) + "\n</tool_environment>",
             "<current_policy>\n" + (json.dumps(current_rules, indent=0) if current_rules else "(no rules)")
             + "\n</current_policy>", "Suggest the rules as the JSON object."]
    allowed = set(tools) | set(http) | {"*"} | {f"agent__{a}" for a in agents}
    return Prepared(text="\n\n".join(parts),
                    data={"allowed": sorted(allowed), "mcp": [m for m in mcp if m], "http": http,
                          "current": [list(_key(r)) for r in current_rules]},
                    diagnostics={"tools": tools, "network": network, "http_tools": len(http),
                                 "current_rules": len(current_rules), "files_listed": len(ctx["files"])})


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    rules = result.get("rules")
    if not isinstance(rules, list) or not rules:
        return ["suggest at least one rule"]
    errs = []
    if len(rules) > MAX_RULES:
        errs.append(f"suggest at most {MAX_RULES} rules (got {len(rules)})")
    allowed, mcp = set(data.get("allowed") or []), data.get("mcp") or []
    current = {tuple(k) for k in data.get("current") or []}
    seen: set[tuple[str, str, str]] = set()
    for i, r in enumerate(rules):
        tool = str(r.get("tool") or "")
        if tool not in allowed and not any(tool.startswith(f"mcp__{m}__") for m in mcp):
            errs.append(f"rule {i + 1}: the agent has no tool {tool!r} (use one of {sorted(allowed)})")
            continue
        try:
            P.parse_policy({"rules": [{**r, "pattern": r.get("pattern") or None}]}, tools=set(data.get("http") or []))
        except ValueError as exc:
            errs.append(f"rule {i + 1}: {C.clip(str(exc), 200)}")
            continue
        if not str(r.get("label") or "").strip():
            errs.append(f"rule {i + 1}: label is empty")
        k = _key(r)
        if k in current:
            errs.append(f"rule {i + 1} ({tool} {k[1]!r}) is already in the policy")
        elif k in seen:
            errs.append(f"rule {i + 1} repeats another suggestion")
        seen.add(k)
    if not str(result.get("explanation") or "").strip():
        errs.append("explanation is empty")
    return errs


def fake(user: str) -> dict[str, Any]:
    import re

    env = re.search(r"<tool_environment>\n(.*?)\n</tool_environment>", user, re.S)
    env_text = env.group(1) if env else ""
    tools = re.search(r"builtin tools: (.*)", env_text)
    have = {t.strip() for t in (tools.group(1) if tools else "").split(",")}
    network = "network: on" in env_text
    cur = re.search(r"<current_policy>\n(.*?)\n</current_policy>", user, re.S)
    cur_rules = json.loads(cur.group(1)) if cur and cur.group(1).startswith("[") else []
    current = {_key(r) for r in cur_rules}
    pool = []
    if "bash" in have:
        pool += P.PRESETS["git_push"] + P.PRESETS["rm_rf"]
        if network:
            pool += P.PRESETS["package_installs"]
    for t in ("write_file", "edit_file"):
        if t in have:
            pool.append({"tool": t, "pattern": "*.env", "mode": "deny", "label": "env files"})
    rules = [{"tool": r["tool"], "pattern": r.get("pattern") or "", "mode": r["mode"], "label": r["label"]}
             for r in pool if _key(r) not in current][:4]
    if not rules:
        rules = [{"tool": "*", "pattern": "", "mode": "ask", "label": "every call"}]
    return {"result": {"rules": rules, "explanation": "Asks before pushes and deletions and protects env files."},
            "rationale": "Fits the agent's tools."}


SPEC = register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=False, needs_agent=True, fake=fake)
