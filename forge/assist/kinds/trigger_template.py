"""Assist kind "trigger_template": the input template of a GitHub or Slack trigger, from the event type.

Input: {"source": "github"|"slack" (required), "event": str (GitHub: issues, issue_comment, pull_request or push;
Slack: slash or mention; optional), "current": str (the template in the box; the new one must differ)}.
Result: {"template": str, "explanation": str}. Templates use `{field}` placeholders from the source's fields only.
"""

from __future__ import annotations

import re
from typing import Any

from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register
from forge.triggers import github as G, slack as S

KIND = "trigger_template"
MAX_TEMPLATE = 4000
FIELD_RE = re.compile(r"\{\{|\}\}|\{([a-z_][a-z0-9_]*)\}")
FIELDS = {"github": sorted(G.SAMPLE), "slack": sorted(S.SAMPLE)}
EVENTS = {"github": ["issues", "issue_comment", "pull_request", "push"], "slack": ["slash", "mention"]}
EVENT_FIELDS = {
    "issues": "number, title, body, url, author, repo, label",
    "issue_comment": "comment (the text after the command), number, title, body, url, sender, repo",
    "pull_request": "number, title, body, pr_url, author, branch, base, head_sha, action, repo",
    "push": "branch, commits (one line per commit), commit_count, compare, head_sha, sender, repo",
    "slash": "text (after the command), user, channel, command, team",
    "mention": "text (the message without the mention), user, channel, team",
}

SYSTEM = """You write the input template of a trigger that starts an AI agent run from a GitHub webhook or a \
Slack message. The rendered template becomes the agent's task, so it must read as a clear request to THIS agent.

- Use only {field} placeholders from the allowed fields listed in the request; write literal braces as {{ and }}.
- Put the essential fields first (what happened, where, by whom, the link), then the body or text, then one or \
two sentences telling the agent what to do with it that fit its purpose (for example triage the issue and \
propose labels, review the diff of the pull request, answer the Slack question), and what to reply.
- Keep it under 1500 characters. No secrets.
- explanation: one sentence on what the template asks the agent to do.

Respond with the JSON object only: {"result": {"template": "...", "explanation": "..."}, "rationale": "..."}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"template": {"type": "string"}, "explanation": {"type": "string"}},
    "required": ["template", "explanation"],
    "additionalProperties": False,
}


def build_context(req: AssistRequest) -> Prepared:
    source = C.text_input(req.input, "source", 20)
    if source not in FIELDS:
        raise ValueError("input.source must be github or slack")
    event = C.text_input(req.input, "event", 40)
    if event and event not in EVENTS[source]:
        raise ValueError(f"input.event must be one of {', '.join(EVENTS[source])}")
    current = C.text_input(req.input, "current", MAX_TEMPLATE)
    ctx, rendered = C.for_request(req)
    lines = [f"source: {source}", f"event: {event or ', '.join(EVENTS[source])}",
             f"allowed fields: {', '.join(FIELDS[source])}"]
    if event:
        lines.append(f"most useful fields for this event: {EVENT_FIELDS[event]}")
    if source == "github" and event in G.DEFAULT_TEMPLATES:
        lines.append(f"default template (improve on it for this agent):\n{G.DEFAULT_TEMPLATES[event]}")
    parts = [rendered, "<trigger>\n" + "\n".join(lines) + "\n</trigger>"]
    if current:
        parts.append(f"<current_template>\n{current}\n</current_template>\nWrite a better or different template.")
    parts.append("Write the template as the JSON object.")
    return Prepared(text="\n\n".join(parts), data={"fields": FIELDS[source], "current": current},
                    diagnostics={"source": source, "event": event or None, "files_listed": len(ctx["files"])})


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    t = result.get("template")
    if not isinstance(t, str) or not t.strip():
        return ["template is empty"]
    errs = []
    if len(t) > MAX_TEMPLATE:
        errs.append(f"template has {len(t)} chars; keep it under {MAX_TEMPLATE}")
    used = [m.group(1) for m in FIELD_RE.finditer(t) if m.group(1)]
    unknown = sorted({u for u in used if u not in data["fields"]})
    if unknown:
        errs.append(f"unknown placeholders {unknown}; allowed: {', '.join(data['fields'])}")
    if not used:
        errs.append("the template uses no {field} placeholder, so every run would get the same input")
    if data.get("current") and C.normalize_text(t) == C.normalize_text(data["current"]):
        errs.append("the template is unchanged; write a different one")
    if not str(result.get("explanation") or "").strip():
        errs.append("explanation is empty")
    return errs


def fake(user: str) -> dict[str, Any]:
    src = re.search(r"^source: (\w+)", user, re.M)
    ev = re.search(r"^event: (\w+)$", user, re.M)
    source, event = (src.group(1) if src else "github"), (ev.group(1) if ev else "")
    if source == "slack":
        t = "Request from {user} in #{channel}: {text}\n\nAnswer in a few sentences."
    else:
        t = G.DEFAULT_TEMPLATES.get(event, "{title}\n{url}\n\n{body}") + "\n\nReply with a short summary of what you did."
    if f"<current_template>\n{t}\n</current_template>" in user:
        t += " Keep it brief."
    return {"result": {"template": t, "explanation": "Gives the agent the event details and what to reply."},
            "rationale": "Fits the trigger event."}


SPEC = register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=False, needs_agent=True, fake=fake,
                result_text=lambda r: str(r.get("template") or ""))
