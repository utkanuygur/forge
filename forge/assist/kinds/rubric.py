"""Assist kind "rubric": a judge rubric for one task input.

Input: {"task": str (required, the case input), "current": str (optional, the rubric already in the box; the
new one must differ)}. Result: {"rubric": str}.
"""

from __future__ import annotations

from typing import Any

from forge.assist import context as C
from forge.assist.kinds.eval_case import JUDGE_STYLE, MAX_CHECKER_CHARS, validate_checker
from forge.assist.registry import AssistRequest, Prepared, register

KIND = "rubric"

SYSTEM = f"""You write grading rubrics for an LLM judge that decides whether an AI agent completed a task. The \
judge sees the task, the agent's final reply and the diff of the agent's workspace (the files listed in the \
request are where it starts).

Write ONE rubric for the given task:
- Open with what to grade (which file in the diff, the final reply, or both).
- Then "Pass only if every point holds:" and a numbered list of 2 to 7 concrete, checkable requirements taken \
from the task and the real workspace (exact file names, functions, figures, sections). Say what correct means; \
avoid vague words like "good", "clear" or "appropriate" unless you define them.
- End with a line starting "Fail if" listing disqualifying mistakes (invented facts, claims the diff does not \
support, edits outside the scope of the task).
- Do not demand wording or formatting the task did not ask for, so any correct solution passes.
- Never include secret values.

Example of the style:
<judge_rubric_example>
{JUDGE_STYLE}
</judge_rubric_example>

Respond with the JSON object only: {{"result": {{"rubric": "..."}}, "rationale": "..."}}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"rubric": {"type": "string"}},
    "required": ["rubric"],
    "additionalProperties": False,
}


def build_context(req: AssistRequest) -> Prepared:
    task = C.text_input(req.input, "task", 3000, required=True)
    current = C.text_input(req.input, "current", MAX_CHECKER_CHARS)
    ctx, rendered = C.for_request(req)
    parts = [rendered, f"<task>\n{task}\n</task>"]
    if current:
        parts.append(f"<current_rubric>\n{current}\n</current_rubric>\nWrite a better or different rubric.")
    parts.append("Write the rubric as the JSON object.")
    return Prepared(text="\n\n".join(parts), data={"current": current},
                    diagnostics={"task_chars": len(task), "current": bool(current), "files_listed": len(ctx["files"])})


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    rubric = result.get("rubric") or ""
    errs = validate_checker("judge", rubric if isinstance(rubric, str) else "", True)
    if isinstance(rubric, str) and rubric and not any(line.lstrip().lower().startswith("fail if")
                                                      for line in rubric.splitlines()):
        errs.append('end the rubric with a line starting "Fail if"')
    if data.get("current") and C.normalize_text(rubric) == C.normalize_text(data["current"]):
        errs.append("the rubric is unchanged; write a different one")
    return errs


def fake(user: str) -> dict[str, Any]:
    import re

    m = re.search(r"<task>\n(.*?)\n</task>", user, re.S)
    task = C.clip(m.group(1) if m else "the task", 120)
    extra = "\n3. It names every file it changed." if "<current_rubric>" in user else ""
    rubric = (f"Grade the final reply and the workspace diff. Pass only if every point holds:\n1. It does what the "
              f"task asks: {task}\n2. Every claim in the reply is supported by the diff or the files.{extra}\n"
              "Fail if it invents facts or edits files the task did not mention.")
    return {"result": {"rubric": rubric}, "rationale": "Concrete criteria taken from the task."}


SPEC = register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=False, needs_agent=True, fake=fake,
                result_text=lambda r: str(r.get("rubric") or ""))
