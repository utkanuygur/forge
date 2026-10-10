"""Assist kinds "eval_case" and "eval_cases": draft evaluation cases (task input plus checker) for an agent.

`eval_case` input (all optional): {"split": "train"|"holdout", "focus": str, "checker_type": "shell"|"judge",
"from_run_id": str, "task": str}. `task` fixes the case input (the Add case form's checker Generate button sends
the input already typed); `from_run_id` turns one of the agent's runs plus its feedback into a case (the input is
then the run's input verbatim). Result: {name, input, checker_type, checker, split, rationale}.

`eval_cases` input: {"n": 1..6 (default 3), "holdout": int (how many of the n are holdout; default n // 2),
"focus": str, "checker_type": "shell"|"judge"}. Result: {"cases": [<eval_case result>, ...]}.

Neither kind saves anything: the client shows the drafts and creates a case only when the user clicks Add.
"""

from __future__ import annotations

import re
from typing import Any

from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register

KIND = "eval_case"
BATCH_KIND = "eval_cases"
MAX_BATCH = 6
MAX_INPUT_CHARS = 3000
MAX_CHECKER_CHARS = 12_000
MAX_NAME_CHARS = 60
MAX_EXISTING = 40
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SPLITS = ("train", "holdout")
CHECKER_TYPES = ("shell", "judge")
# Commands that need the internet; a shell checker for an offline agent must not use them.
NETWORK_RE = re.compile(r"\b(curl|wget|pip3? install|npm (?:install|i)\b|apt(?:-get)? install|git clone|uv add)\b")

SHELL_STYLE = """\
set -euo pipefail
fail() { echo "FAIL: $*"; exit 1; }
test -f report.md || fail "report.md was not written"
grep -qiE 'total' report.md || fail "report.md does not mention the total"
python3 - <<'PY'
import json, sys
data = json.load(open("out/summary.json"))
if data.get("rows") != 42:
    sys.exit(f"FAIL: summary.json rows is {data.get('rows')!r}, expected 42")
PY
log=$(mktemp)
make test > "$log" 2>&1 || { tail -n 40 "$log"; fail "make test"; }
echo PASS"""

JUDGE_STYLE = """\
Grade REPORT.md in the workspace diff and the final reply. Pass only if every point holds:
1. It names the three slowest endpoints from logs/access.log with their p95 latency in ms.
2. ...
Fail if any figure is not supported by the data, or if it edits files other than REPORT.md."""

CASE_RULES = f"""An evaluation case is a task input sent to the agent plus a checker that decides pass or fail after \
the agent finishes. The agent runs in an isolated Linux sandbox; its working directory /workspace starts with the \
files listed in the request, and it works with the tools listed there.

Rules for every case:
- input: a concrete, self contained task in the agent's purpose, phrased as a teammate's request (1 to 6 \
sentences), naming the real files, functions, endpoints, columns or log entries shown in the context. Never invent \
data the context does not show (the task may ask to create new files that build on what exists). It must be \
solvable in the sandbox with the given files and tools; when network access is off it must not need the internet, \
downloads or external APIs. Never put secret values in it; refer to configured secrets by NAME only.
- Cover a gap: different in substance from every existing case and every text in the avoid list (another \
problem, file or skill, not a rewording). Existing train cases show which skills matter.
- checker_type "shell" only when success is checkable by commands in the sandbox after the run (files written, \
tests passing, exit codes, exact values in output files). A shell checker runs with cwd /workspace, exit code 0 \
means pass, and it is written defensively: start with `set -euo pipefail`, define \
`fail() {{ echo "FAIL: $*"; exit 1; }}`, check each requirement in turn and print one `FAIL: <what is wrong>` line \
for the first failure, then `echo PASS`. Prefer exact checks (python3 heredocs that load JSON or CSV, grep -q \
for required text, running the project's tests or make targets and printing the tail of their log on failure). \
It must FAIL on the untouched workspace (the task is not done yet) and PASS for any correct solution, so do not \
over specify wording or formatting the task did not ask for. Never rely on files the agent could edit to define \
what correct means, and never use the network when network access is off. Example of the style:
<shell_checker_example>
{SHELL_STYLE}
</shell_checker_example>
- checker_type "judge" for anything that needs reading comprehension (quality of writing, explanations, \
reviews, analysis). The checker is then a rubric for an LLM judge that sees the task, the agent's final reply and \
the workspace diff. Write it as concrete criteria: open with what to grade, then "Pass only if every point holds:" \
and a numbered list of checkable requirements (exact names, figures, files), then a line starting "Fail if" for \
disqualifying mistakes. No vague words like "good" or "clear" without saying what that means. Example:
<judge_rubric_example>
{JUDGE_STYLE}
</judge_rubric_example>
- name: a short lowercase slug of 2 to 6 words joined by hyphens (e.g. "orders-refund-endpoint"), unique among \
the existing cases.
- split: "train" cases are what the agent is improved on; "holdout" cases guard against overfitting. A holdout \
case should be a different task that exercises the same skills as the train cases (another file, endpoint or \
dataset), never a variant of a train case's wording.
- rationale: one or two sentences on what the case checks and which gap it covers. It must not quote the system \
prompt."""

SYSTEM = f"""You write evaluation cases for an AI agent so its builder can measure and improve it.

{CASE_RULES}

Write ONE case that follows the request (split, checker type, focus or source run when given). Respond with the \
JSON object only: {{"result": {{"name": "...", "input": "...", "checker_type": "shell|judge", "checker": "...", \
"split": "train|holdout", "rationale": "..."}}, "rationale": "<one sentence summary>"}}"""

BATCH_SYSTEM = f"""You write a balanced set of evaluation cases for an AI agent so its builder can measure and \
improve it.

{CASE_RULES}

Write exactly the requested number of cases with exactly the requested number of train and holdout cases. The \
cases must differ from each other in substance (each covers another skill, file or failure mode) and have unique \
names. Respond with the JSON object only: {{"result": {{"cases": [{{"name": "...", "input": "...", \
"checker_type": "shell|judge", "checker": "...", "split": "train|holdout", "rationale": "..."}}]}}, \
"rationale": "<one sentence on what the set covers>"}}"""

CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "input": {"type": "string"},
        "checker_type": {"type": "string", "enum": list(CHECKER_TYPES)},
        "checker": {"type": "string"},
        "split": {"type": "string", "enum": list(SPLITS)},
        "rationale": {"type": "string"},
    },
    "required": ["name", "input", "checker_type", "checker", "split", "rationale"],
    "additionalProperties": False,
}
BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"cases": {"type": "array", "items": CASE_SCHEMA}},
    "required": ["cases"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- data access (monkeypatched in unit tests)


def load_cases(agent_id: str) -> list[dict]:
    from forge import db

    return db.list_eval_cases(agent_id)


def load_run(run_id: str) -> tuple[dict | None, list[dict]]:
    """(run, feedback) for a run id."""
    from forge import db

    run = db.get_run(run_id)
    return run, (db.list_feedback(run_id) if run else [])


# ---------------------------------------------------------------- context


def _opt(value: Any, allowed: tuple[str, ...], what: str) -> str | None:
    if value in (None, ""):
        return None
    if value not in allowed:
        raise ValueError(f"{what} must be one of {', '.join(allowed)}")
    return value


def _existing_block(cases: list[dict]) -> str:
    if not cases:
        return "<existing_cases>\n(none yet)\n</existing_cases>"
    lines = []
    for c in cases[-MAX_EXISTING:]:
        lines.append(f"- [{c.get('split')}, {c.get('checker_type')}] {c.get('name')}: {C.clip(c.get('input', ''), 300)}")
    more = f"\n({len(cases) - MAX_EXISTING} older cases not shown)" if len(cases) > MAX_EXISTING else ""
    return "<existing_cases>\n" + "\n".join(lines) + more + "\n</existing_cases>"


def _shell_example(cases: list[dict]) -> str:
    """The agent's own best shell checker (the longest under 4000 chars) as a style reference."""
    shells = [c for c in cases if c.get("checker_type") == "shell" and 0 < len(c.get("checker") or "") <= 4000]
    if not shells:
        return ""
    best = max(shells, key=lambda c: len(c["checker"]))
    return f'<existing_shell_checker name="{best.get("name")}">\n{best["checker"]}\n</existing_shell_checker>'


def _common(req: AssistRequest) -> tuple[dict, list[dict], list[str], list[str]]:
    assert req.agent is not None and req.version is not None
    ctx = C.agent_context(req.agent, req.version, recent_inputs=req.recent_inputs, secret_names=req.secret_names)
    cases = load_cases(req.agent["id"]) if req.agent.get("id") else []
    parts = [C.render_agent_context(ctx), _existing_block(cases)]
    if ex := _shell_example(cases):
        parts.append(ex)
    avoid = C.dedupe([a for a in req.avoid if isinstance(a, str)], C.MAX_AVOID)
    if avoid:
        parts.append(C.bullets("avoid", avoid))
    earlier = [t for t in C.dedupe(req.recent_results, C.MAX_RECENT) if t not in avoid]
    if earlier:
        parts.append(C.bullets("earlier_suggestions", earlier))
    return ctx, cases, parts, avoid


def _source_run(req: AssistRequest, run_id: str) -> tuple[dict, str]:
    run, feedback = load_run(run_id)
    if run is None or run.get("agent_id") != req.agent.get("id"):  # type: ignore[union-attr]
        raise ValueError(f"run {run_id!r} not found for this agent")
    notes = [f"- rating {'+1' if (f.get('rating') or 0) > 0 else '-1'}: {C.clip(f.get('comment') or '(no comment)', 500)}"
             for f in feedback]
    block = (f"<source_run id=\"{run_id}\" status=\"{run.get('status')}\">\n"
             f"<source_run_input>\n{run.get('input') or ''}\n</source_run_input>\n"
             f"<source_run_output>\n{C.clip(run.get('output') or '(no reply)', 2500)}\n</source_run_output>\n"
             "<feedback>\n" + ("\n".join(notes) or "(no feedback)") + "\n</feedback>\n</source_run>")
    return run, block


def build_context(req: AssistRequest) -> Prepared:
    inp = req.input or {}
    split = _opt(inp.get("split"), SPLITS, "split")
    checker_type = _opt(inp.get("checker_type"), CHECKER_TYPES, "checker_type")
    focus = inp.get("focus").strip()[:500] if isinstance(inp.get("focus"), str) else ""
    task = inp.get("task").strip()[:MAX_INPUT_CHARS] if isinstance(inp.get("task"), str) else ""
    run_id = inp.get("from_run_id") if isinstance(inp.get("from_run_id"), str) and inp.get("from_run_id") else None
    ctx, cases, parts, avoid = _common(req)
    fixed_input = ""
    if run_id:
        run, block = _source_run(req, run_id)
        fixed_input = (run.get("input") or "").strip()
        parts.append(block)
    elif task:
        fixed_input = task
    req_lines = [f"split: {split or 'train'}",
                 f"checker_type: {checker_type or 'choose shell when the result is checkable by commands, else judge'}"]
    if focus:
        req_lines.append(f"focus: {focus}")
    if run_id:
        req_lines.append("source: turn the source run into a case. Use the run's input VERBATIM as the case input. "
                         "The checker must encode what a correct result looks like, using the feedback: negative "
                         "feedback names what was wrong, so the checker must fail on that mistake; positive feedback "
                         "confirms the behavior to keep. Never copy feedback text into the input.")
    elif task:
        req_lines.append("task: the case input is fixed (see fixed_input); use it VERBATIM as input and write the "
                         "checker and name for it.")
        parts.append(f"<fixed_input>\n{task}\n</fixed_input>")
    if (split or "train") == "holdout":
        train = [c for c in cases if c.get("split") == "train"]
        if train:
            req_lines.append("holdout: write a different task that exercises the same skills as the train cases "
                             "above, not a variant of any of them.")
    parts.append("<request>\n" + "\n".join(req_lines) + "\n</request>")
    parts.append("Write the evaluation case as the JSON object.")
    existing_inputs = [c.get("input") or "" for c in cases]
    return Prepared(
        text="\n\n".join(parts),
        data={"split": split or "train", "checker_type": checker_type, "existing_inputs": existing_inputs,
              "existing_names": [c.get("name") or "" for c in cases], "avoid": avoid, "fixed_input": fixed_input,
              "network": ctx["network"]},
        diagnostics={"existing_cases": len(cases), "from_run_id": run_id, "split": split or "train",
                     "checker_type": checker_type, "files_listed": len(ctx["files"]),
                     "excerpts": [p for p, _ in ctx["excerpts"]], "avoid": len(avoid)})


def batch_split(inp: dict[str, Any]) -> tuple[int, int]:
    """(n, holdout) from a batch input, validated: n in 1..6 (default 3), holdout in 0..n (default n // 2)."""
    n = inp.get("n", 3)
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_BATCH:
        raise ValueError(f"n must be an integer from 1 to {MAX_BATCH}")
    holdout = inp.get("holdout", n // 2)
    if not isinstance(holdout, int) or isinstance(holdout, bool) or not 0 <= holdout <= n:
        raise ValueError("holdout must be an integer from 0 to n")
    return n, holdout


def build_batch_context(req: AssistRequest) -> Prepared:
    inp = req.input or {}
    n, holdout = batch_split(inp)
    checker_type = _opt(inp.get("checker_type"), CHECKER_TYPES, "checker_type")
    focus = inp.get("focus").strip()[:500] if isinstance(inp.get("focus"), str) else ""
    ctx, cases, parts, avoid = _common(req)
    req_lines = [f"count: {n} cases, {n - holdout} train and {holdout} holdout",
                 f"checker_type: {checker_type or 'per case: shell when checkable by commands, else judge'}"]
    if focus:
        req_lines.append(f"focus: {focus}")
    if holdout:
        req_lines.append("holdout cases: different tasks that exercise the same skills as the train cases (existing "
                         "and new), not variants of them.")
    parts.append("<request>\n" + "\n".join(req_lines) + "\n</request>")
    parts.append("Write the evaluation cases as the JSON object.")
    return Prepared(
        text="\n\n".join(parts),
        data={"n": n, "holdout": holdout, "checker_type": checker_type,
              "existing_inputs": [c.get("input") or "" for c in cases],
              "existing_names": [c.get("name") or "" for c in cases], "avoid": avoid, "network": ctx["network"]},
        diagnostics={"existing_cases": len(cases), "n": n, "holdout": holdout, "checker_type": checker_type,
                     "files_listed": len(ctx["files"]), "avoid": len(avoid)})


# ---------------------------------------------------------------- validation


def validate_checker(checker_type: str, checker: str, network: bool) -> list[str]:
    errs = []
    if not checker:
        return ["checker is empty"]
    if len(checker) > MAX_CHECKER_CHARS:
        errs.append(f"checker has {len(checker)} chars; keep it under {MAX_CHECKER_CHARS}")
    if checker_type == "shell":
        if "set -euo pipefail" not in checker:
            errs.append("a shell checker must start with `set -euo pipefail`")
        if "FAIL" not in checker:
            errs.append("a shell checker must print a `FAIL: ...` line explaining each failure")
        if not network and (m := NETWORK_RE.search(checker)):
            errs.append(f"network access is off, but the shell checker uses {m.group(0)!r}")
    else:
        if not re.search(r"pass only if", checker, re.I):
            errs.append('a judge rubric must state "Pass only if every point holds:" followed by numbered criteria')
        if len(re.findall(r"(?m)^\s*(?:\d+[.)]|[-*])\s+\S", checker)) < 2:
            errs.append("a judge rubric needs at least two concrete numbered criteria")
    return errs


def validate_case(r: dict[str, Any], data: dict[str, Any], *, split: str | None, others: list[str],
                  other_names: list[str], prefix: str = "") -> list[str]:
    errs: list[str] = []
    name, text = r.get("name"), r.get("input")
    if not isinstance(name, str) or not NAME_RE.match(name) or len(name) > MAX_NAME_CHARS:
        errs.append(f"{prefix}name {name!r} must be a lowercase slug of words joined by hyphens, under "
                    f"{MAX_NAME_CHARS} chars")
    elif name in other_names:
        errs.append(f"{prefix}name {name!r} is already used; pick a unique name")
    if not isinstance(text, str) or not text:
        errs.append(f"{prefix}input is empty")
    elif len(text) > MAX_INPUT_CHARS:
        errs.append(f"{prefix}input has {len(text)} chars; keep it under {MAX_INPUT_CHARS}")
    elif data.get("fixed_input"):
        if C.normalize_text(text) != C.normalize_text(data["fixed_input"]):
            errs.append(f"{prefix}input must be the given task verbatim")
    elif (dup := C.near_duplicate(text, others)) is not None:
        errs.append(f"{prefix}input nearly duplicates an existing case or avoided text ({C.clip(dup, 160)!r}); "
                    "cover a different gap")
    ct = r.get("checker_type")
    if ct not in CHECKER_TYPES:
        errs.append(f"{prefix}checker_type must be shell or judge")
    elif data.get("checker_type") and ct != data["checker_type"]:
        errs.append(f"{prefix}checker_type must be {data['checker_type']}")
    else:
        errs += [prefix + e for e in validate_checker(ct, r.get("checker") or "", bool(data.get("network")))]
    if r.get("split") not in SPLITS:
        errs.append(f"{prefix}split must be train or holdout")
    elif split and r.get("split") != split:
        errs.append(f"{prefix}split must be {split}")
    if not isinstance(r.get("rationale"), str) or not r.get("rationale"):
        errs.append(f"{prefix}rationale is empty")
    return errs


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    return validate_case(result, data, split=data.get("split"),
                         others=data.get("existing_inputs", []) + data.get("avoid", []),
                         other_names=data.get("existing_names", []))


def validate_batch(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    cases = result.get("cases")
    if not isinstance(cases, list) or not all(isinstance(c, dict) for c in cases):
        return ["cases must be a list of case objects"]
    n, holdout = data["n"], data["holdout"]
    errs = []
    if len(cases) != n:
        errs.append(f"write exactly {n} cases (got {len(cases)})")
    got_holdout = sum(1 for c in cases if c.get("split") == "holdout")
    if got_holdout != holdout:
        errs.append(f"write exactly {holdout} holdout and {n - holdout} train cases (got {got_holdout} holdout)")
    others = data.get("existing_inputs", []) + data.get("avoid", [])
    names = list(data.get("existing_names", []))
    for i, c in enumerate(cases):
        errs += validate_case(c, data, split=None, others=others, other_names=names, prefix=f"case {i + 1}: ")
        if isinstance(c.get("input"), str):
            others = others + [c["input"]]
        if isinstance(c.get("name"), str):
            names.append(c["name"])
    return errs


# ---------------------------------------------------------------- fake:grader

FAKE_SHELL = [
    {"name": "workspace-summary-file",
     "input": "Write SUMMARY.md with one line per file in the workspace saying what it is for, as a markdown list.",
     "checker": 'set -euo pipefail\nfail() { echo "FAIL: $*"; exit 1; }\ntest -f SUMMARY.md || fail "SUMMARY.md '
                'was not written"\ngrep -qE \'^\\s*[-*] \' SUMMARY.md || fail "SUMMARY.md has no list items"\n'
                'echo PASS',
     "rationale": "Checks that the agent reads the workspace and writes a file; no existing case covers this."},
    {"name": "line-count-report",
     "input": "Count the lines in each file of the workspace and save the counts to counts.json as an object "
              "mapping path to line count.",
     "checker": 'set -euo pipefail\nfail() { echo "FAIL: $*"; exit 1; }\ntest -f counts.json || fail "counts.json '
                'was not written"\npython3 - <<\'PY\'\nimport json, sys\ndata = json.load(open("counts.json"))\n'
                'if not isinstance(data, dict) or not data:\n    sys.exit("FAIL: counts.json is not a non empty '
                'object")\nPY\necho PASS',
     "rationale": "Checks exact figures saved to a machine readable file."},
    {"name": "hello-script-runs",
     "input": "Add hello.py that prints Hello from Forge and make sure it runs.",
     "checker": 'set -euo pipefail\nfail() { echo "FAIL: $*"; exit 1; }\ntest -f hello.py || fail "hello.py was '
                'not written"\nout=$(python3 hello.py) || fail "hello.py exits non zero"\n'
                'echo "$out" | grep -q "Hello from Forge" || fail "unexpected output: $out"\necho PASS',
     "rationale": "Checks creating and running a small script."},
    {"name": "directory-tree-file",
     "input": "Save the directory tree of the workspace to TREE.txt, one path per line, sorted.",
     "checker": 'set -euo pipefail\nfail() { echo "FAIL: $*"; exit 1; }\ntest -s TREE.txt || fail "TREE.txt was '
                'not written"\nsort -c TREE.txt 2>/dev/null || fail "TREE.txt is not sorted"\necho PASS',
     "rationale": "Checks listing files and sorting output exactly."},
    {"name": "todo-inventory-json",
     "input": "Find every TODO or FIXME comment in the workspace and write todos.json as a list of objects with "
              "file, line and text.",
     "checker": 'set -euo pipefail\nfail() { echo "FAIL: $*"; exit 1; }\ntest -f todos.json || fail "todos.json was '
                'not written"\npython3 - <<\'PY\'\nimport json, sys\ndata = json.load(open("todos.json"))\n'
                'if not isinstance(data, list):\n    sys.exit("FAIL: todos.json is not a list")\nPY\necho PASS',
     "rationale": "Checks searching code and emitting structured findings."},
    {"name": "extension-histogram-csv",
     "input": "Write extensions.csv with columns extension and count for the file types in the workspace.",
     "checker": 'set -euo pipefail\nfail() { echo "FAIL: $*"; exit 1; }\ntest -f extensions.csv || fail '
                '"extensions.csv was not written"\nhead -n 1 extensions.csv | grep -qx "extension,count" || fail '
                '"extensions.csv header is not extension,count"\necho PASS',
     "rationale": "Checks aggregating data into a CSV with an exact header."},
]
FAKE_JUDGE = [
    {"name": "explain-workspace-layout",
     "input": "Explain in a short reply how the workspace is organized and which file to read first.",
     "checker": "Grade the final reply. Pass only if every point holds:\n1. It names at least two real files from "
                "the workspace.\n2. It recommends one file to read first and says why.\nFail if it mentions files "
                "that do not exist.",
     "rationale": "Checks reading comprehension of the workspace with a concrete rubric."},
    {"name": "review-largest-file",
     "input": "Review the largest file in the workspace and reply with three concrete improvement suggestions.",
     "checker": "Grade the final reply. Pass only if every point holds:\n1. It identifies the largest file by "
                "path.\n2. It gives exactly three suggestions, each tied to a specific line or section.\nFail if a "
                "suggestion is generic advice not tied to the file.",
     "rationale": "Checks a review task graded by rubric."},
]


def _tag(user: str, key: str) -> str | None:
    """The value of `key: ...` inside the <request> block of an assist request."""
    m = re.search(r"<request>\n(.*?)\n</request>", user, re.S)
    if not m:
        return None
    k = re.search(rf"^{key}: (\S+)", m.group(1), re.M)
    return k.group(1) if k else None


def _fake_pick(user: str, checker_type: str | None, taken: set[str]) -> dict[str, Any]:
    pools = [FAKE_JUDGE] if checker_type == "judge" else [FAKE_SHELL] if checker_type == "shell" else \
        [FAKE_SHELL, FAKE_JUDGE]
    for pool in pools:
        for c in pool:
            if c["name"] not in taken and c["input"] not in user:
                return {**c, "checker_type": "judge" if pool is FAKE_JUDGE else "shell"}
    pool = pools[0]
    c = pool[len(taken) % len(pool)]
    n = 2
    while f"{c['name']}-{n}" in taken:
        n += 1
    return {**c, "name": f"{c['name']}-{n}", "input": f"{c['input']} (variant {n})",
            "checker_type": "judge" if pool is FAKE_JUDGE else "shell"}


def _existing_names(user: str) -> set[str]:
    m = re.search(r"<existing_cases>\n(.*?)\n</existing_cases>", user, re.S)
    return set(re.findall(r"^- \[[^\]]*\] ([a-z0-9-]+):", m.group(1), re.M)) if m else set()


def fake(user: str) -> dict[str, Any]:
    ct = _tag(user, "checker_type")
    ct = ct if ct in CHECKER_TYPES else None
    split = _tag(user, "split") or "train"
    case = _fake_pick(user, ct, _existing_names(user))
    run = re.search(r"<source_run_input>\n(.*?)\n</source_run_input>", user, re.S)
    task = re.search(r"<fixed_input>\n(.*?)\n</fixed_input>", user, re.S)
    if run or task:
        text = (run or task).group(1).strip()  # type: ignore[union-attr]
        fb = re.search(r"<feedback>\n(.*?)\n</feedback>", user, re.S)
        case = {**case, "name": "from-run-" + (C.normalize_text(text).replace(" ", "-")[:30].strip("-") or "case")
                if run else case["name"], "input": text}
        if run and fb and "(no feedback)" not in fb.group(1) and case["checker_type"] == "judge":
            case["checker"] += "\n3. It addresses the reviewer feedback on the earlier attempt."
    return {"result": {**case, "split": split}, "rationale": case["rationale"]}


def fake_batch(user: str) -> dict[str, Any]:
    m = re.search(r"count: (\d+) cases, (\d+) train and (\d+) holdout", user)
    n, train = (int(m.group(1)), int(m.group(2))) if m else (3, 2)
    ct = _tag(user, "checker_type")
    ct = ct if ct in CHECKER_TYPES else None
    taken = _existing_names(user)
    out = []
    for i in range(n):
        c = _fake_pick(user + "\n".join(x["input"] for x in out), ct, taken)
        taken.add(c["name"])
        out.append({**c, "split": "train" if i < train else "holdout"})
    return {"result": {"cases": out}, "rationale": f"{n} cases covering different skills."}


SPEC = register(KIND, build_context, SYSTEM, CASE_SCHEMA, validate, share_allowed=False, needs_agent=True,
                fake=fake, result_text=lambda r: str(r.get("input") or ""))
BATCH_SPEC = register(BATCH_KIND, build_batch_context, BATCH_SYSTEM, BATCH_SCHEMA, validate_batch,
                      share_allowed=False, needs_agent=True, fake=fake_batch,
                      result_text=lambda r: " | ".join(str(c.get("input") or "") for c in r.get("cases") or []
                                                       if isinstance(c, dict)))
