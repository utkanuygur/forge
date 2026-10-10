"""Assist kind "version_note": the "What changed?" rationale for a new version, from its diff against production.

Input: {"config": {system_prompt?, model?, tools?, network?, seed_files? (path -> content), http_tools?,
mcp_servers?, callable_agents?, approval_policy?, max_run_cost_usd?}} (the unsaved draft; missing keys mean
unchanged). The diff is computed against the request's version (production by default); no change is a 400.
Result: {"note": str}.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register

KIND = "version_note"
MAX_NOTE = 300
MAX_PROMPT_DIFF = 6000

SYSTEM = """You write the short version note that explains a change to an AI agent's configuration, for the \
version history its team reads. You are given the agent and the exact differences between the draft and the \
production version.

Write ONE note: a single sentence or two (under 300 characters, no line breaks) that says what changed and why \
it likely matters, most important change first, in plain past tense or imperative ("Requires running the tests \
before replying; adds the orders CSV"). Name concrete things (tools, files, rules) instead of generic words like \
"updates" or "improvements". Only describe changes that appear in the diff. No dashes as punctuation, no secret \
values, no quotes from the instructions longer than a few words.

Respond with the JSON object only: {"result": {"note": "..."}, "rationale": "..."}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"note": {"type": "string"}},
    "required": ["note"],
    "additionalProperties": False,
}


def _names(items: Any) -> set[str]:
    return {str(i.get("name")) if isinstance(i, dict) else str(i) for i in items or []}


def diff_config(base: dict[str, Any], draft: dict[str, Any]) -> list[str]:
    """Human readable differences, one string per change; keys missing from `draft` are unchanged."""
    out: list[str] = []
    if "system_prompt" in draft and (draft["system_prompt"] or "") != (base.get("system_prompt") or ""):
        lines = difflib.unified_diff((base.get("system_prompt") or "").splitlines(),
                                     (draft["system_prompt"] or "").splitlines(), "production", "draft", lineterm="",
                                     n=1)
        text = "\n".join(list(lines)[2:])
        out.append("instructions changed:\n" + (text[:MAX_PROMPT_DIFF] + ("\n[... diff cut ...]"
                                                                           if len(text) > MAX_PROMPT_DIFF else "")))
    if "model" in draft and draft["model"] and draft["model"] != base.get("model"):
        out.append(f"model: {base.get('model')} -> {draft['model']}")
    if "tools" in draft:
        a, b = set(base.get("tools") or []), set(draft["tools"] or [])
        if a != b:
            out.append(f"builtin tools: added {sorted(b - a) or 'none'}, removed {sorted(a - b) or 'none'}")
    if "network" in draft and bool(draft["network"]) != bool(base.get("network")):
        out.append(f"network access: {'on' if draft['network'] else 'off'} (was {'on' if base.get('network') else 'off'})")
    if "seed_files" in draft:
        a, b = base.get("seed_files") or {}, draft["seed_files"] or {}
        added, removed = sorted(set(b) - set(a)), sorted(set(a) - set(b))
        changed = sorted(p for p in set(a) & set(b) if a[p] != b[p])
        if added or removed or changed:
            out.append(f"seed files: added {added[:20] or 'none'}, removed {removed[:20] or 'none'}, "
                       f"edited {changed[:20] or 'none'}")
    for key, label in (("http_tools", "HTTP tools"), ("mcp_servers", "MCP servers")):
        if key in draft:
            a_items = {str(t.get("name")): t for t in base.get(key) or [] if isinstance(t, dict)}
            b_items = {str(t.get("name")): t for t in draft[key] or [] if isinstance(t, dict)}
            added, removed = sorted(set(b_items) - set(a_items)), sorted(set(a_items) - set(b_items))
            changed = sorted(n for n in set(a_items) & set(b_items)
                             if json.dumps(a_items[n], sort_keys=True) != json.dumps(b_items[n], sort_keys=True))
            if added or removed or changed:
                out.append(f"{label}: added {added or 'none'}, removed {removed or 'none'}, edited {changed or 'none'}")
    if "callable_agents" in draft and _names(draft["callable_agents"]) != _names(base.get("callable_agents")):
        out.append(f"callable agents: {sorted(_names(base.get('callable_agents')))} -> "
                   f"{sorted(_names(draft['callable_agents']))}")
    if "approval_policy" in draft:
        def rules(p: Any) -> list[str]:
            return sorted(f"{r.get('mode', 'ask')} {r.get('tool')} {r.get('pattern') or ''}".strip()
                          for r in ((p or {}).get("rules") or []) if isinstance(r, dict))
        a, b = rules(base.get("approval_policy")), rules(draft["approval_policy"])
        if a != b:
            out.append(f"approval rules: added {sorted(set(b) - set(a)) or 'none'}, "
                       f"removed {sorted(set(a) - set(b)) or 'none'}")
    if "max_run_cost_usd" in draft and draft["max_run_cost_usd"] != base.get("max_run_cost_usd"):
        out.append(f"per run cost cap: {base.get('max_run_cost_usd')} -> {draft['max_run_cost_usd']} USD")
    return out


def build_context(req: AssistRequest) -> Prepared:
    assert req.agent is not None and req.version is not None
    draft = req.input.get("config")
    if not isinstance(draft, dict):
        raise ValueError("input.config (the draft configuration) is required")
    changes = diff_config(req.version, draft)
    if not changes:
        raise ValueError("the draft has no changes from the base version")
    ctx, rendered = C.for_request(req)
    parts = [rendered, f"<changes base_version=\"{req.version.get('n')}\">\n" + "\n\n".join(changes) + "\n</changes>",
             "Write the version note as the JSON object."]
    return Prepared(text="\n\n".join(parts), data={},
                    diagnostics={"changes": [c.split(":", 1)[0] for c in changes], "base_version": req.version.get("n")})


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    note = result.get("note")
    if not isinstance(note, str) or not note:
        return ["note is empty"]
    errs = []
    if len(note) > MAX_NOTE:
        errs.append(f"note has {len(note)} chars; keep it under {MAX_NOTE}")
    if "\n" in note:
        errs.append("note must be one paragraph without line breaks")
    if re.search(r"\s[-–—]{1,2}\s", note):
        errs.append("do not use dashes as punctuation in the note")
    return errs


def fake(user: str) -> dict[str, Any]:
    m = re.search(r"<changes[^>]*>\n(.*?)\n</changes>", user, re.S)
    areas = re.findall(r"^([a-zA-Z][a-zA-Z ]+?):", m.group(1) if m else "", re.M)
    areas = [a for a in areas if a in ("instructions changed", "model", "builtin tools", "network access",
                                       "seed files", "HTTP tools", "MCP servers", "callable agents",
                                       "approval rules", "per run cost cap")]
    names = ", ".join(a.replace(" changed", "") for a in areas) or "configuration"
    note = f"Changes {names}."
    return {"result": {"note": note[:MAX_NOTE]}, "rationale": "Summarizes the diff against production."}


SPEC = register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=False, needs_agent=True, fake=fake,
                result_text=lambda r: str(r.get("note") or ""))
