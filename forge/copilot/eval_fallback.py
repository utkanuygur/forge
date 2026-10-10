"""Private fallback for the assistant's generate_eval_cases tool: grader-written eval cases about a topic.

Replace with feat/assist kind at merge: feat/assist adds the public assist kinds `eval_case` and `eval_cases`,
and forge.copilot.evalgen prefers them. This module registers its own kind, `copilot_eval_cases`, only while
neither of those exists, so the two branches never register the same name.

Input: {"focus": str (what the cases should test), "count": 1..5 (default 3), "split": "train" | "holdout"
(optional; default: the model picks, about two thirds train)}. Result: {"cases": [{name, input, checker_type,
checker, split}]}. Nothing is saved: the caller creates the cases it keeps with POST /agents/{slug}/evals.
"""

from __future__ import annotations

import re
from typing import Any

from forge import db
from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register

KIND = "copilot_eval_cases"
MAX_COUNT = 5
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

SYSTEM = """You write evaluation cases for an AI agent. Each case is a task the agent receives in a fresh \
sandbox (with the workspace files listed in the request) plus a checker that decides whether the agent \
succeeded. Forge runs every case several times and keeps the majority verdict, so a case must be deterministic \
to check.

Write exactly the requested number of NEW cases about the requested topic. Requirements:
- name: a short unique slug (lowercase letters, digits, dashes), different from every existing case name.
- input: a concrete, self-contained request in 1 to 4 sentences, phrased like a real user, naming the real \
files, functions, endpoints or columns from the context. Never invent files the workspace does not have \
unless the task asks the agent to create them. Never include secret values.
- checker_type "shell" when success can be verified by a command in the workspace after the run (exit code 0 \
means pass, for example `make check`, `pytest -q tests/test_x.py`, `grep -q ... file`); the checker is that \
command (1 to 500 chars). Otherwise checker_type "judge" with a rubric of 60 to 1200 chars that starts with \
"PASS only if" and names what to look for in the final answer or the changed files.
- split: "train" for cases the optimizer may learn from, "holdout" for cases that only measure; follow the \
requested split when one is given, else about two thirds train.
- Different in substance from the existing cases listed in the request.

Give a one sentence rationale saying what the cases cover.

Respond with the JSON object only: {"result": {"cases": [{"name": "...", "input": "...", "checker_type": \
"shell", "checker": "...", "split": "train"}]}, "rationale": "..."}"""

CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "input": {"type": "string"},
                   "checker_type": {"type": "string", "enum": ["shell", "judge"]}, "checker": {"type": "string"},
                   "split": {"type": "string", "enum": ["train", "holdout"]}},
    "required": ["name", "input", "checker_type", "checker", "split"],
    "additionalProperties": False,
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"cases": {"type": "array", "items": CASE_SCHEMA}},
    "required": ["cases"],
    "additionalProperties": False,
}


def parse_count(raw: Any) -> int:
    try:
        n = int(raw) if raw is not None else 3
    except (TypeError, ValueError):
        raise ValueError("count must be a number from 1 to 5") from None
    if not 1 <= n <= MAX_COUNT:
        raise ValueError("count must be a number from 1 to 5")
    return n


def _words(topic: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", topic.lower())[:4]) or "case"


def fake(user: str) -> dict[str, Any]:
    """`fake:grader` reply: `count` judge cases about the topic, named after it, never reusing a listed name."""
    count = int(m.group(1)) if (m := re.search(r"<count>\s*(\d+)", user)) else 3
    topic = m.group(1).strip() if (m := re.search(r"<topic>\s*(.*?)\s*</topic>", user, re.S)) else "the agent"
    split = m.group(1) if (m := re.search(r"<split>\s*(train|holdout)", user)) else None
    existing = set(re.findall(r"^- name: (\S+)", user, re.M))
    base, cases, i = _words(topic), [], 1
    while len(cases) < count:
        name = f"{base}-{i}"
        i += 1
        if name in existing:
            continue
        cases.append({"name": name, "input": f"Handle this request about {topic} (variant {len(cases) + 1}) and "
                                             f"explain what you changed.",
                      "checker_type": "judge",
                      "checker": f"PASS only if the final answer addresses {topic} directly, names the files it "
                                 f"changed or read, and reports a concrete result.",
                      "split": split or ("holdout" if len(cases) == 2 else "train")})
    return {"result": {"cases": cases}, "rationale": f"Covers {topic}."}


def build_context(req: AssistRequest) -> Prepared:
    assert req.agent is not None and req.version is not None
    topic = req.input.get("focus")
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError(f"{KIND} needs input.focus")
    topic = topic.strip()[:500]
    count = parse_count(req.input.get("count"))
    split = req.input.get("split")
    if split not in (None, "", "train", "holdout"):
        raise ValueError("split must be train or holdout")
    existing = db.list_eval_cases(req.agent["id"]) if req.agent.get("id") else []
    ctx = C.agent_context(req.agent, req.version, recent_inputs=req.recent_inputs, secret_names=req.secret_names)
    parts = [C.render_agent_context(ctx)]
    if existing:
        parts.append("<existing_cases>\n" + "\n".join(
            f"- name: {c['name']}\n  split: {c['split']}\n  input: {C.clip(c['input'], 240)}" for c in existing[:40])
            + "\n</existing_cases>")
    parts.append(f"<topic>\n{topic}\n</topic>")
    parts.append(f"<count>{count}</count>")
    if split:
        parts.append(f"<split>{split}</split>")
    parts.append("Write the eval cases as the JSON object.")
    return Prepared(text="\n\n".join(parts),
                    data={"count": count, "split": split or None, "existing": [c["name"] for c in existing],
                          "existing_inputs": [c["input"] for c in existing]},
                    diagnostics={"topic": topic, "count": count, "existing_cases": len(existing)})


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    cases = result.get("cases")
    if not isinstance(cases, list) or not cases:
        return ["cases is empty"]
    errs = []
    if len(cases) != data.get("count", len(cases)):
        errs.append(f"wrote {len(cases)} cases; write exactly {data.get('count')}")
    seen = set(data.get("existing", []))
    for i, c in enumerate(cases, 1):
        if not isinstance(c, dict):
            errs.append(f"case {i} is not an object")
            continue
        name, text, kind, checker = c.get("name", ""), c.get("input", ""), c.get("checker_type"), c.get("checker", "")
        if not isinstance(name, str) or not NAME_RE.match(name):
            errs.append(f"case {i} name {name!r} must be a lowercase slug")
        elif name in seen:
            errs.append(f"case {i} name {name!r} is already used")
        seen.add(name)
        if not isinstance(text, str) or not text.strip():
            errs.append(f"case {i} input is empty")
        elif C.near_duplicate(text, data.get("existing_inputs", [])):
            errs.append(f"case {i} input nearly duplicates an existing case")
        if kind == "shell":
            if not (isinstance(checker, str) and 1 <= len(checker) <= 500):
                errs.append(f"case {i} shell checker must be 1 to 500 chars")
        elif kind == "judge":
            if not (isinstance(checker, str) and 60 <= len(checker) <= 1200):
                errs.append(f"case {i} judge rubric must be 60 to 1200 chars")
        else:
            errs.append(f"case {i} checker_type must be shell or judge")
        if c.get("split") not in ("train", "holdout"):
            errs.append(f"case {i} split must be train or holdout")
        elif data.get("split") and c.get("split") != data["split"]:
            errs.append(f"case {i} split must be {data['split']}")
    return errs


def ensure_registered() -> str | None:
    """Registers the fallback kind unless feat/assist's kinds exist. Returns this kind's name when it is active."""
    from forge.assist import registry

    if registry.get(KIND) is not None:
        return KIND
    if registry.get("eval_cases") is not None or registry.get("eval_case") is not None:
        return None
    register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=False, needs_agent=True, fake=fake)
    return KIND
