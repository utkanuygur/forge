"""`fake:copilot`: a deterministic stand-in for the assistant's model (also used by `fake:grader` whenever it is
called with the assistant's system prompt), so the assistant can be tested end to end without API calls.

Given the latest user message (after its <context> block):
  "add N [train|holdout] eval cases to <slug>"   N create_eval_case calls in one turn (one confirmation card),
                                                 then a summary; after a denial, says nothing was added
  "why did run <r_id> fail"                      get_run, then an answer quoting the run's status and error
  anything about secrets, API keys or tokens     a polite refusal, no tool calls
  anything else                                  a short explanation of what the assistant can do
N may be a digit or a word from one to ten. Tool call ids are unique per user message.
"""

from __future__ import annotations

import re

from forge.types import Message, ModelResponse, ToolCall

NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "a": 1, "an": 1}
ADD = re.compile(r"\badd\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|an?)\s+(?:(train|holdout)\s+)?"
                 r"eval(?:uation)?\s+cases?\b.*?\b(?:to|for)\s+([a-z0-9][a-z0-9-]*)", re.I | re.S)
WHY = re.compile(r"\bwhy\b.*\brun\s+(r_[0-9a-f]{12})\b.*\bfail", re.I | re.S)
SECRET = re.compile(r"\b(secrets?|api[ _-]?keys?|passwords?|tokens?)\b", re.I)

HELP = ("I can explain how Forge works and act on it for you: list agents and runs, explain failures, add or "
        "generate eval cases, run evaluations and improvements, promote versions, set approval rules, budgets and "
        "schedules. Anything that changes data or costs money waits for your approval.")
REFUSAL = ("I can't view, set or reveal secrets or API keys. You can manage secrets on the agent's "
           "[Configure tab](/agents) and rotate API keys on its Deploy tab.")


def _text(text: str) -> ModelResponse:
    return ModelResponse(text=text, tokens_in=30, tokens_out=10, stop_reason="end_turn")


def respond(messages: list[Message]) -> ModelResponse:
    last_user = max(i for i, m in enumerate(messages) if m.role == "user")
    turn = sum(1 for m in messages if m.role == "user") - 1
    step = sum(1 for m in messages[last_user:] if m.role == "assistant")
    raw = messages[last_user].text
    text = raw.split("</context>", 1)[-1].strip()
    results = [m for m in messages[last_user:] if m.role == "tool"]

    if m := ADD.search(text):
        n = int(m.group(1)) if m.group(1).isdigit() else NUMBERS[m.group(1).lower()]
        n = max(1, min(n, 10))
        split, slug = (m.group(2) or "train").lower(), m.group(3)
        if step == 0:
            calls = [ToolCall(id=f"call_t{turn}_{i}", name="create_eval_case", args={
                "slug": slug, "name": f"assistant-case-{turn}-{i + 1}",
                "input": f"Create hello.py that prints a greeting, then run it (case {i + 1}).",
                "checker_type": "shell", "checker": "test -f hello.py", "split": split}) for i in range(n)]
            return ModelResponse(tool_calls=calls, notes=[f"Adding {n} eval cases to {slug}."], tokens_in=30,
                                 tokens_out=10, stop_reason="tool_use")
        ok = [r for r in results if not r.is_error]
        if not ok:
            reason = results[0].text if results else "no result"
            if "denied" in reason.lower() or "declined" in reason.lower():
                return _text(f"Nothing was added: you declined, so {slug} keeps its current eval cases.")
            return _text(f"I could not add the eval cases: {reason}")
        names = ", ".join(r.text.split(" ", 2)[-1].split(" [")[0] for r in ok)
        return _text(f"Added {len(ok)} eval cases to `{slug}`: {names}. See them on the "
                     f"[Evaluations tab](/agents/{slug}/evals).")

    if m := WHY.search(text):
        run_id = m.group(1)
        if step == 0:
            return ModelResponse(tool_calls=[ToolCall(id=f"call_t{turn}_0", name="get_run", args={"run_id": run_id})],
                                 tokens_in=30, tokens_out=10, stop_reason="tool_use")
        out = results[-1].text if results else ""
        if results and results[-1].is_error:
            return _text(f"I could not load run {run_id}: {out}")
        status = re.search(r"^status: (.*)$", out, re.M)
        error = re.search(r"^error: (.*)$", out, re.M)
        return _text(f"Run [{run_id}](/runs/{run_id}) ended **{status.group(1) if status else 'unknown'}**. "
                     f"The error was: {error.group(1) if error else 'not recorded'}.")

    if SECRET.search(text):
        return _text(REFUSAL)
    return _text(HELP)
