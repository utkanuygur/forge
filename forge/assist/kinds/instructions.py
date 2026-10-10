"""Assist kind "instructions": an improved version of the agent's instructions (system prompt).

Input (all optional): {"current": str (the unsaved text in the Configure box; default the version's prompt),
"goal": str (what to change, e.g. "make it stricter about tests")}. Result: {"instructions": str, "summary": str}.
The web shows the result as a diff that the user accepts or rejects; nothing is saved.
"""

from __future__ import annotations

import re
from typing import Any

from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register

KIND = "instructions"
MAX_CHARS = 20_000
MAX_SUMMARY = 300
MAX_FEEDBACK = 10
MAX_FAILING = 10

SYSTEM = """You improve the instructions (system prompt) of an AI agent that runs in an isolated Linux sandbox \
with the tools and files listed in the request. You are given the current instructions, the agent's context, \
recent negative feedback from its users and the evaluation cases it currently fails.

Return the complete improved instructions. Requirements:
- Keep the agent's role, purpose and every constraint that still applies; keep what already works. Change only \
what makes the agent more reliable: a clear method, conventions it must follow (from the workspace files), how to \
verify its work before replying, and what the final reply must contain.
- Address the problems behind the feedback and the failing cases as general rules. Never mention evaluation \
case names, never copy checker or rubric text and never hard code the answer to one known task.
- Respect the environment: only the listed tools, no internet when network access is off, secrets referred to \
by NAME only (never invent or include secret values).
- If a goal is given, apply it. If the current instructions are empty, write complete instructions from the \
agent's name, description and files.
- Plain, direct second person ("You ..."), short sections or lists where they help, no filler. Keep the length \
between half and twice the current length unless the current text is very short or empty.

Put the full text in result.instructions and one or two sentences on what changed and why in result.summary. \
Respond with the JSON object only: {"result": {"instructions": "...", "summary": "..."}, "rationale": "..."}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"instructions": {"type": "string"}, "summary": {"type": "string"}},
    "required": ["instructions", "summary"],
    "additionalProperties": False,
}


def load_signals(agent_id: str, version_id: str | None) -> tuple[list[str], list[dict], list[str]]:
    """(recent negative feedback comments, failing eval cases [{name, reason}], every case name) for the
    agent's version."""
    from forge import db

    comments = [f"{C.clip(f.get('run_input') or '', 160)} => {C.clip(f.get('comment') or '', 300)}"
                for f in db.negative_feedback_since(agent_id, 0.0) if f.get("comment")]
    failing: list[dict] = []
    names = {c["id"]: c["name"] for c in db.list_eval_cases(agent_id)}
    if version_id:
        for r in db.list_eval_results(version_id):
            if not r.get("passed") and r.get("case_id") in names:
                failing.append({"name": names[r["case_id"]], "reason": C.clip(r.get("reason") or "", 300)})
    return comments[-MAX_FEEDBACK:], failing[:MAX_FAILING], list(names.values())


def build_context(req: AssistRequest) -> Prepared:
    assert req.agent is not None and req.version is not None
    current = req.input.get("current")
    current = current[:MAX_CHARS] if isinstance(current, str) else (req.version.get("system_prompt") or "")
    goal = C.text_input(req.input, "goal", 1000)
    ctx, rendered = C.for_request(req, system_prompt="(the current instructions are given below)")
    feedback, failing, case_names = load_signals(req.agent.get("id") or "", req.version.get("id"))
    parts = [rendered, f"<current_instructions>\n{current or '(empty)'}\n</current_instructions>"]
    lessons = [str(x) for x in req.version.get("lessons") or []]
    if lessons:
        parts.append(C.bullets("learned_lessons", lessons[:20]) +
                     "\n(Lessons are appended to the instructions automatically; do not copy them in.)")
    if feedback:
        parts.append(C.bullets("negative_feedback", feedback))
    if failing:
        parts.append(C.bullets("failing_cases", [f"{f['name']}: {f['reason']}" for f in failing]))
    if goal:
        parts.append(f"<goal>\n{goal}\n</goal>")
    parts.append("Write the improved instructions as the JSON object.")
    return Prepared(text="\n\n".join(parts),
                    data={"current": current, "case_names": case_names},
                    diagnostics={"current_chars": len(current), "feedback": len(feedback), "failing": len(failing),
                                 "goal": bool(goal), "files_listed": len(ctx["files"])})


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    text, summary = result.get("instructions"), result.get("summary")
    errs = []
    if not isinstance(text, str) or not text:
        return ["instructions are empty"]
    if len(text) > MAX_CHARS:
        errs.append(f"instructions have {len(text)} chars; keep them under {MAX_CHARS}")
    current = data.get("current") or ""
    if C.normalize_text(text) == C.normalize_text(current):
        errs.append("instructions are unchanged; improve them")
    if len(current) >= 400 and not len(current) * 0.4 <= len(text) <= len(current) * 2.5:
        errs.append(f"instructions have {len(text)} chars against {len(current)} now; keep between half and twice "
                    "the current length")
    for name in data.get("case_names") or []:
        if len(name) >= 6 and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
            errs.append(f"instructions mention the evaluation case {name!r}; write general rules instead")
    if not isinstance(summary, str) or not summary:
        errs.append("summary is empty")
    elif len(summary) > MAX_SUMMARY:
        errs.append(f"summary has {len(summary)} chars; keep it under {MAX_SUMMARY}")
    return errs


FAKE_LINES = ("Always finish with a one line summary of what you changed.",
              "Run the project's tests before you reply and report the result.")


def fake(user: str) -> dict[str, Any]:
    m = re.search(r"<current_instructions>\n(.*?)\n</current_instructions>", user, re.S)
    current = (m.group(1) if m else "").strip()
    current = "" if current == "(empty)" else current
    line = next((x for x in FAKE_LINES if x not in current), FAKE_LINES[0] + " Keep it short.")
    summary = "Adds a closing summary requirement." if line == FAKE_LINES[0] else "Adds a test step before replying."
    text = f"{current}\n\n{line}" if current else f"You are a careful assistant.\n\n{line}"
    return {"result": {"instructions": text, "summary": summary}, "rationale": summary}


SPEC = register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=False, needs_agent=True, fake=fake)
