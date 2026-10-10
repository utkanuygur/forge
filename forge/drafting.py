"""Agent drafting: turn a plain description of an agent into a reviewable draft configuration.

One grader-model call with structured output (`output_config.format` on Claude; a JSON-only instruction plus
parsing elsewhere) returns a draft; the draft is validated and the call is retried once with the validation
errors when it is invalid. Nothing is created here: the builder reviews and edits the draft, then creates the
agent and the selected starter eval cases with `POST /v1/agents`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from forge.types import Message, Provider

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SECRET_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
CASE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
BUILTIN_TOOLS = ("bash", "read_file", "write_file", "edit_file")
MIN_CASES, MAX_CASES = 3, 6
MIN_PROMPT_CHARS, MAX_PROMPT_CHARS = 300, 8000
MIN_RUBRIC_CHARS, MAX_RUBRIC_CHARS = 60, 1200
MAX_SHELL_CHARS = 500
MAX_DESCRIPTION_CHARS = 8000


class DraftError(Exception):
    """The model did not produce a valid draft after one retry. `errors` lists what was wrong."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or []


# ---------------------------------------------------------------- prompt

DRAFT_SYSTEM = """You design AI agents for Forge. A Forge agent is a system prompt plus a model running in an \
isolated Linux sandbox whose working directory is a workspace of files. Built-in tools: bash (run shell \
commands), read_file, write_file, edit_file (replace an exact snippet). Network access is off unless enabled; \
secrets arrive as environment variables. Each run gets one task as its input; the agent works with the tools and \
ends with a final written reply. Agents improve later from evaluation cases with train and held-out splits.

From the builder's description (and any example tasks and constraints), draft the agent's configuration and \
3 to 6 starter evaluation cases.

Write the system_prompt as the agent's operating manual, in second person, 150 to 600 words, with these parts:
1. Role and goal: who the agent is and what a finished task looks like, in terms specific to this domain.
2. Working method: numbered steps for how to approach a task here (what to inspect first, how to do the work, \
how to verify it before finishing). Name the concrete files, commands, formats or sources the work involves.
3. Output: exactly what to produce (which files and their format, and what the final reply must contain).
4. Avoid: the specific mistakes an agent in this domain is likely to make (for example inventing data, \
skipping verification, editing unrelated files, leaking secrets).
Never write generic filler such as "be helpful", "be thorough" or "do your best". Every sentence must change \
what the agent does. Do not mention Forge, evaluation cases or graders in the system prompt.

Choose tools as the smallest set the work needs (most agents that produce files need bash, read_file and \
write_file; add edit_file when they modify existing files). Enable network only if the task needs the internet \
(web research, external APIs); give a one-line reason either way. Suggest secrets only for credentials the \
description implies (environment variable names in UPPER_SNAKE_CASE, never values), each with why it is needed.

Starter evaluation cases:
- Each case is a realistic task input the agent should handle, varied in difficulty, self-contained, and not \
copied from the examples word for word.
- About two thirds train and one third holdout, with at least one of each.
- Use checker_type "judge" with a rubric written as concrete pass/fail criteria that a grader can verify from \
the final reply and the workspace diff, e.g. "PASS only if: (1) report.md exists; (2) it lists every overdue \
invoice with its amount; (3) totals match the input data. FAIL if any figure is invented." Never write vague \
rubrics like "the answer is good".
- Use checker_type "shell" only when the result is a file whose correctness a command can check without the \
model's help (exit code 0 means pass), e.g. "test -f summary.csv && python -c \\"import csv; rows=list(csv.reader(\
open('summary.csv'))); assert rows[0]==['region','total']\\"". The command runs in the final workspace without \
network. Only rely on files the task input itself creates or tells the agent to create.
- Case names are short lowercase slugs (letters, digits, dashes).

Also give a short name, a lowercase slug (letters, digits, dashes), a one-sentence description, and \
seed_files_hint: one or two sentences on which files the builder should add as seed files (or "None needed.").

Example (compact). Description: "Summarize our weekly sales CSV exports into a short report for managers."
{"name": "Sales report writer", "slug": "sales-report-writer",
 "description": "Turns weekly sales CSV exports into a short manager report.",
 "system_prompt": "You are a sales analyst who turns weekly CSV exports in your workspace into a one page \
report for managers. A task is done when report.md states the week's key numbers correctly and explains them.\\n\\n\
How to work:\\n1. List the workspace and open each CSV; note columns, row counts, date range and missing values.\\n\
2. Compute every figure with a Python script saved as analysis.py; never estimate by eye.\\n3. Cross check \
totals two ways (by region and overall) and investigate any mismatch.\\n4. Write report.md: a three line summary, \
a table of revenue by region with week over week change, then notable anomalies.\\n\\nFinal reply: the three \
headline numbers and where the report is.\\n\\nAvoid: inventing numbers or regions not in the data, silently \
dropping rows with missing values (count and mention them), and editing the input CSVs.",
 "tools": ["bash", "read_file", "write_file"], "network": false,
 "network_reason": "All input arrives as files in the workspace.", "suggested_secrets": [],
 "seed_files_hint": "Add one recent CSV export so every run starts with realistic data.",
 "eval_cases": [{"name": "regional-totals", "input": "...", "checker_type": "judge", "checker": "PASS only if \
report.md exists, gives revenue for every region in the CSV, and the overall total equals the sum of the rows. \
FAIL if any number is not derivable from the data.", "split": "train"}, "..."]}

Respond with the JSON object only."""


DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "slug": {"type": "string"},
        "description": {"type": "string"},
        "system_prompt": {"type": "string"},
        "tools": {"type": "array", "items": {"type": "string", "enum": list(BUILTIN_TOOLS)}},
        "network": {"type": "boolean"},
        "network_reason": {"type": "string"},
        "suggested_secrets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "why": {"type": "string"}},
                "required": ["name", "why"],
                "additionalProperties": False,
            },
        },
        "seed_files_hint": {"type": "string"},
        "eval_cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "input": {"type": "string"},
                    "checker_type": {"type": "string", "enum": ["judge", "shell"]},
                    "checker": {"type": "string"},
                    "split": {"type": "string", "enum": ["train", "holdout"]},
                },
                "required": ["name", "input", "checker_type", "checker", "split"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["name", "slug", "description", "system_prompt", "tools", "network", "network_reason",
                 "suggested_secrets", "seed_files_hint", "eval_cases"],
    "additionalProperties": False,
}


def draft_request(description: str, examples: list[str] | None = None, constraints: str | None = None) -> str:
    parts = [f"<description>\n{description.strip()}\n</description>"]
    for i, ex in enumerate(e for e in (examples or []) if e.strip()):
        parts.append(f'<example_task n="{i + 1}">\n{ex.strip()}\n</example_task>')
    if constraints and constraints.strip():
        parts.append(f"<constraints>\n{constraints.strip()}\n</constraints>")
    parts.append("Draft the agent and its starter evaluation cases as the JSON object.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- validation


def normalize(obj: dict[str, Any]) -> dict[str, Any]:
    """Coerces a raw model object into the draft shape (string trimming, defaults). Does not validate."""
    def s(v: Any) -> str:
        return v.strip() if isinstance(v, str) else ""

    cases = []
    for c in obj.get("eval_cases") or []:
        if isinstance(c, dict):
            cases.append({"name": s(c.get("name")), "input": s(c.get("input")),
                          "checker_type": s(c.get("checker_type")), "checker": s(c.get("checker")),
                          "split": s(c.get("split"))})
    secrets = []
    for x in obj.get("suggested_secrets") or []:
        if isinstance(x, dict):
            secrets.append({"name": s(x.get("name")), "why": s(x.get("why"))})
    tools = obj.get("tools")
    return {
        "name": s(obj.get("name")), "slug": s(obj.get("slug")), "description": s(obj.get("description")),
        "system_prompt": s(obj.get("system_prompt")),
        "tools": [t for t in tools if isinstance(t, str)] if isinstance(tools, list) else [],
        "network": obj.get("network") if isinstance(obj.get("network"), bool) else None,
        "network_reason": s(obj.get("network_reason")),
        "suggested_secrets": secrets, "seed_files_hint": s(obj.get("seed_files_hint")),
        "eval_cases": cases,
    }


def validate_draft(d: dict[str, Any]) -> list[str]:
    """Returns human-readable problems; an empty list means the draft is valid."""
    errs: list[str] = []
    if not d["name"]:
        errs.append("name is empty")
    if not SLUG_RE.match(d["slug"]):
        errs.append(f"slug {d['slug']!r} must be lowercase letters, digits and dashes (max 63 chars)")
    n = len(d["system_prompt"])
    if not MIN_PROMPT_CHARS <= n <= MAX_PROMPT_CHARS:
        errs.append(f"system_prompt has {n} chars; it must have {MIN_PROMPT_CHARS} to {MAX_PROMPT_CHARS}")
    unknown = [t for t in d["tools"] if t not in BUILTIN_TOOLS]
    if unknown:
        errs.append(f"unknown tools {unknown}; choose from {list(BUILTIN_TOOLS)}")
    if not d["tools"]:
        errs.append("tools is empty")
    if d["network"] is None:
        errs.append("network must be true or false")
    if not d["network_reason"]:
        errs.append("network_reason is empty")
    for x in d["suggested_secrets"]:
        if not SECRET_RE.match(x["name"]):
            errs.append(f"secret name {x['name']!r} must be UPPER_SNAKE_CASE")
        if not x["why"]:
            errs.append(f"secret {x['name']!r} needs a reason")
    cases = d["eval_cases"]
    if not MIN_CASES <= len(cases) <= MAX_CASES:
        errs.append(f"{len(cases)} eval cases; write {MIN_CASES} to {MAX_CASES}")
    names = [c["name"] for c in cases]
    if len(set(names)) != len(names):
        errs.append("eval case names must be unique")
    for c in cases:
        label = c["name"] or "(unnamed case)"
        if not CASE_NAME_RE.match(c["name"]):
            errs.append(f"case name {label!r} must be a lowercase slug")
        if not c["input"]:
            errs.append(f"case {label}: input is empty")
        if c["split"] not in ("train", "holdout"):
            errs.append(f"case {label}: split must be train or holdout")
        if c["checker_type"] == "judge":
            if not MIN_RUBRIC_CHARS <= len(c["checker"]) <= MAX_RUBRIC_CHARS:
                errs.append(f"case {label}: judge rubric has {len(c['checker'])} chars; write concrete pass/fail "
                            f"criteria in {MIN_RUBRIC_CHARS} to {MAX_RUBRIC_CHARS} chars")
        elif c["checker_type"] == "shell":
            if not c["checker"] or len(c["checker"]) > MAX_SHELL_CHARS:
                errs.append(f"case {label}: shell checker must be 1 to {MAX_SHELL_CHARS} chars")
        else:
            errs.append(f"case {label}: checker_type must be judge or shell")
    splits = [c["split"] for c in cases]
    if cases and "holdout" not in splits:
        errs.append("at least one eval case must be in the holdout split")
    if cases and "train" not in splits:
        errs.append("at least one eval case must be in the train split")
    if cases and splits.count("holdout") * 2 > len(cases):
        errs.append("about two thirds of the eval cases should be train; too many are holdout")
    return errs


# ---------------------------------------------------------------- model call


async def _complete_structured(grader: Provider, system: str, user: str) -> str:
    """One grader call that returns JSON text. Claude gets `output_config.format` (structured outputs), other
    providers get the same prompt and are parsed leniently."""
    from forge.providers import MAX_TOKENS, AnthropicProvider, with_retry

    if isinstance(grader, AnthropicProvider):
        kwargs: dict[str, Any] = {
            "model": grader.model, "max_tokens": MAX_TOKENS, "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
        }
        resp = await with_retry(lambda: grader.client.messages.create(**kwargs))
        if getattr(resp, "stop_reason", None) == "refusal":
            raise DraftError("The model declined to draft this agent. Rephrase the description and try again.")
        return "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    resp = await grader.complete(system, [Message(role="user", text=user)], [], temperature=0)
    return resp.text


def _parse(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    from forge.improve import _first_json_object

    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        obj = _first_json_object(text or "")
    if not isinstance(obj, dict):
        return None, ["the reply was not a JSON object"]
    d = normalize(obj)
    return d, validate_draft(d)


async def draft_agent(description: str, grader: Provider, *, examples: list[str] | None = None,
                      constraints: str | None = None) -> dict[str, Any]:
    """Returns a validated draft (see CONTRACT.md "Agent drafts"). Retries once with the validation errors."""
    if not description.strip():
        raise ValueError("description is required")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"description is too long (max {MAX_DESCRIPTION_CHARS} chars)")
    request = draft_request(description, examples, constraints)
    text = await _complete_structured(grader, DRAFT_SYSTEM, request)
    draft, errs = _parse(text)
    if draft is not None and not errs:
        return draft
    # Retry once as a fresh request (no assistant prefill or edited history) that shows the problems.
    retry = (f"{request}\n\n<previous_attempt>\n{(text or '')[:12000]}\n</previous_attempt>\n\n"
             "<problems>\n" + "\n".join(f"- {e}" for e in errs) + "\n</problems>\n\n"
             "The previous attempt was invalid. Return a corrected, complete JSON object that fixes every problem.")
    text = await _complete_structured(grader, DRAFT_SYSTEM, retry)
    draft, errs = _parse(text)
    if draft is None or errs:
        raise DraftError("The model returned an invalid draft twice: " + "; ".join(errs[:6]), errs)
    return draft
