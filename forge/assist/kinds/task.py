"""Assist kind "task": one realistic task a real user of the agent would send (the Generate button).

Input (all optional): {"hint": str, "current": str (the text already in the box, treated as one more thing to
avoid)}. Result: {"title": str, "prompt": str}.
"""

from __future__ import annotations

from typing import Any

from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register

KIND = "task"
MAX_PROMPT_CHARS = 1200
MAX_TITLE_CHARS = 100

# Cycled by the number of earlier generations for the agent so consecutive calls vary in type and difficulty.
VARIETY = (
    "a focused fix of one specific, concrete problem (medium difficulty)",
    "a quick question that needs reading a few files to answer precisely (easy)",
    "a small new capability or endpoint that follows the existing conventions (medium)",
    "an investigation or report built from the data, logs or code, with exact figures (medium)",
    "a multi-step change that touches several files and must be verified, e.g. with the tests (hard)",
    "a review or audit for one class of mistakes, with concrete findings and fixes (medium)",
)

SYSTEM = """You write realistic sample tasks for an AI agent so its builder and colleagues can try it out. The \
agent runs in an isolated Linux sandbox whose working directory holds the files listed in the request; it works \
with the tools listed there and ends with a written reply.

Write ONE task that a real user of this agent would plausibly send next. Requirements:
- Concrete and specific: name the real files, functions, endpoints, columns or log entries from the workspace \
listing and excerpts. Never invent files, endpoints or data that the context does not show (a task may ask the \
agent to create a new file or endpoint, as long as it builds on what exists).
- Self-contained and solvable inside the sandbox with the given files and tools. If network access is off, the \
task must not need the internet, package downloads or external APIs. Never require credentials other than the \
configured secret names listed in the request, and never include secret values.
- Fits the agent's purpose as described by its name, description and system prompt.
- Phrased as a request from a teammate, in 1 to 4 sentences, in plain language, without preamble like "Task:".
- Different from every recent input, every text in the avoid list and every earlier suggestion, in substance \
(a different problem, file or capability) and not just wording.
- Match the requested variety (type and difficulty) when it fits this agent; otherwise pick the nearest fit.

Put a short title (2 to 8 words, no trailing period) and the task text in result. Give a one sentence rationale \
that says what the task exercises; it must not quote the system prompt or reveal configuration details.

Respond with the JSON object only: {"result": {"title": "...", "prompt": "..."}, "rationale": "..."}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "prompt": {"type": "string"}},
    "required": ["title", "prompt"],
    "additionalProperties": False,
}

# What `fake:grader` returns for this kind: the first of these whose prompt is not already in the request (recent
# inputs or the avoid list), so repeated generations differ deterministically; when all appear, the first one.
FAKE_TASKS: list[dict[str, str]] = [
    {"title": "Summarize the workspace",
     "prompt": "Can you list every file in the workspace and write SUMMARY.md with one line per file saying what "
               "it is for?",
     "rationale": "Exercises reading the workspace and writing a file."},
    {"title": "Count lines of code",
     "prompt": "Please count the lines in each file of the workspace and reply with a table sorted from largest "
               "to smallest.",
     "rationale": "Exercises running shell commands and reporting exact figures."},
    {"title": "Write a hello script",
     "prompt": "Add a hello.py that prints a greeting, run it, and tell me the output.",
     "rationale": "Exercises creating and running a small script."},
]


def fake(user: str) -> dict[str, Any]:
    t = next((t for t in FAKE_TASKS if t["prompt"] not in user), FAKE_TASKS[0])
    return {"result": {"title": t["title"], "prompt": t["prompt"]}, "rationale": t["rationale"]}


def build_context(req: AssistRequest) -> Prepared:
    assert req.agent is not None and req.version is not None
    ctx = C.agent_context(req.agent, req.version, recent_inputs=req.recent_inputs, secret_names=req.secret_names)
    current = req.input.get("current")
    hint = req.input.get("hint")
    avoid = C.dedupe([*req.avoid, *([current] if isinstance(current, str) else [])], C.MAX_AVOID)
    variety = VARIETY[req.generation % len(VARIETY)]
    hint = hint.strip()[:500] if isinstance(hint, str) else ""
    parts = [C.render_agent_context(ctx)]
    if avoid:
        parts.append(C.bullets("avoid", avoid))
    earlier = [t for t in C.dedupe(req.recent_results, C.MAX_RECENT) if t not in avoid]
    if earlier:
        parts.append(C.bullets("earlier_suggestions", earlier))
    parts.append(f"<variety>\n{variety}\n</variety>")
    if hint:
        parts.append(f"<hint>\n{hint}\n</hint>")
    parts.append("Write the sample task as the JSON object.")
    return Prepared(
        text="\n\n".join(parts),
        data={"recent_inputs": ctx["recent_inputs"], "avoid": avoid},
        diagnostics={"variety": variety, "files_listed": len(ctx["files"]), "total_files": ctx["total_files"],
                     "excerpts": [p for p, _ in ctx["excerpts"]],
                     "excerpt_chars": sum(len(e) for _, e in ctx["excerpts"]),
                     "system_prompt_chars": ctx["system_prompt_chars"], "recent_inputs": len(ctx["recent_inputs"]),
                     "avoid": len(avoid), "earlier_suggestions": len(earlier)})


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    title, prompt = result.get("title"), result.get("prompt")
    errs = []
    if not isinstance(prompt, str) or not prompt:
        errs.append("prompt is empty")
    elif len(prompt) > MAX_PROMPT_CHARS:
        errs.append(f"prompt has {len(prompt)} chars; keep it under {MAX_PROMPT_CHARS} (1 to 4 sentences)")
    if not isinstance(title, str) or not title:
        errs.append("title is empty")
    elif len(title) > MAX_TITLE_CHARS:
        errs.append(f"title has {len(title)} chars; use 2 to 8 words")
    if isinstance(prompt, str) and prompt:
        dup = C.near_duplicate(prompt, data.get("recent_inputs", []) + data.get("avoid", []))
        if dup is not None:
            errs.append(f"prompt nearly duplicates an earlier input ({C.clip(dup, 160)!r}); write a different task")
    return errs


SPEC = register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=True, needs_agent=True, fake=fake,
                result_text=lambda r: str(r.get("prompt") or ""))
