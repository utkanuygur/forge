"""Improvement logic: eval checking, LLM judge, lesson optimizer and the promotion rule."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from forge.types import AgentVersion, Executor, Message, Provider

log = logging.getLogger("forge.improve")

MAX_LESSONS = 15
MAX_LESSON_CHARS = 300
LEAK_NGRAM = 6
MAX_CONFLICTS = 10
MAX_CONFLICT_CHARS = 600


class LessonEdit(BaseModel):
    op: Literal["add", "edit", "remove"]
    text: str  # new lesson text (for remove: the lesson being removed)
    old_text: str | None = None  # for edit: the lesson being replaced
    evidence_run_ids: list[str] = Field(default_factory=list)
    rationale: str = ""


class InstructionConflict(BaseModel):
    """A place where the author's instructions themselves cause the failures or feedback. Lessons never override
    instructions, so these are reported to the author instead of being turned into lessons."""

    instruction_quote: str  # the instruction, quoted from the system prompt ("" when the check could not quote it)
    problem: str
    suggested_change: str = ""
    evidence_run_ids: list[str] = Field(default_factory=list)
    rejected_lesson: str | None = None  # set when this conflict came from a lesson that was dropped


class Proposal(BaseModel):
    """Validated optimizer output: lesson edits to apply and instruction conflicts to show the author."""

    lessons: list[LessonEdit] = Field(default_factory=list)
    instruction_conflicts: list[InstructionConflict] = Field(default_factory=list)


# ---------------------------------------------------------------- helpers


def _clip(text: str, limit: int) -> str:
    """Keep head and tail of `text` within roughly `limit` chars."""
    if len(text) <= limit:
        return text
    half = max(limit // 2 - 20, 0)
    return f"{text[:half]}\n...[{len(text) - 2 * half} chars truncated]...\n{text[-half:]}"


def _first_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object embedded anywhere in `text` (prose, code fences, etc.)."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def _leaks(text: str, cases: list[dict]) -> bool:
    """True if a lesson mentions a case name or copies 6+ consecutive words of a checker/rubric."""
    low = text.lower()
    grams = _ngrams(_words(text), LEAK_NGRAM)
    for case in cases:
        name = (case.get("name") or "").strip().lower()
        if len(name) >= 3 and name in low:
            return True
        if grams & _ngrams(_words(case.get("checker") or ""), LEAK_NGRAM):
            return True
    return False


# ---------------------------------------------------------------- checking

JUDGE_SYSTEM = """You are a strict, impartial grader for an AI agent's work. You decide whether the agent's \
result satisfies a rubric. You never do the task yourself and you never give partial credit.

How to grade:
1. Read the rubric. Every requirement in it must be met; it is the only standard. Do not invent extra \
requirements and do not relax stated ones.
2. Judge the evidence: the workspace diff shows what the agent actually changed; the final output is what it \
told the user. Claims in the output that the diff does not support count as unmet. If the rubric concerns the \
written answer rather than files, grade the output.
3. If the evidence is missing, truncated past the point you need, or ambiguous on a required point, fail.
4. Pass only if you would confidently sign off on the result as meeting every rubric requirement.

Respond with ONLY a JSON object, no prose and no code fences:
{"passed": true or false, "reason": "<one or two sentences naming the decisive requirement met or missed>"}"""


async def judge(rubric: str, task: str, output: str, diff: str, grader: Provider) -> tuple[bool, str]:
    prompt = (
        f"<rubric>\n{rubric}\n</rubric>\n\n"
        f"<task>\n{_clip(task, 4000)}\n</task>\n\n"
        f"<final_output>\n{_clip(output, 6000)}\n</final_output>\n\n"
        f"<workspace_diff>\n{_clip(diff, 12000) or '(no file changes)'}\n</workspace_diff>\n\n"
        "Grade the result against the rubric. Reply with the JSON object only."
    )
    resp = await grader.complete(JUDGE_SYSTEM, [Message(role="user", text=prompt)], [], temperature=0)
    obj = _first_json_object(resp.text)
    if obj is None or not isinstance(obj.get("passed"), bool):
        return False, f"judge output unparseable: {resp.text[:200]}"
    return obj["passed"], str(obj.get("reason", "")).strip() or ("passed" if obj["passed"] else "failed")


async def check_case(case: dict, executor: Executor, output: str, grader: Provider) -> tuple[bool, str]:
    if case.get("checker_type") == "shell":
        res = await executor.exec(case["checker"], timeout=300)
        if res.exit_code == 0:
            return True, "passed"
        tail = (res.stdout + ("\n" if res.stdout and res.stderr else "") + res.stderr).strip()[-800:]
        prefix = "checker timed out. " if res.timed_out else f"exit {res.exit_code}. "
        return False, prefix + (tail or "(no output)")
    try:
        res = await executor.exec("git add -A && git diff --cached baseline")
        diff = res.stdout if res.exit_code == 0 else ""
    except Exception:
        diff = ""
    return await judge(case["checker"], case.get("input", ""), output, diff, grader)


# ---------------------------------------------------------------- traces


def _render_event(ev: dict) -> str:
    t, p = ev.get("type"), ev.get("payload") or {}
    if t == "run_started":
        return f"[start] model={p.get('model', '?')}\nINPUT: {_clip(str(p.get('input', '')), 2000)}"
    if t == "assistant":
        return f"[assistant] {_clip(str(p.get('text', '')), 2000)}"
    if t == "tool_call":
        args = p.get("args", {})
        rendered = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
        return f"[call {p.get('name', '?')}] {_clip(rendered, 1500)}"
    if t == "tool_result":
        flag = " ERROR" if p.get("is_error") else ""
        return f"[result {p.get('name', '?')}{flag}] {_clip(str(p.get('output', '')), 1500)}"
    if t == "run_finished":
        return (
            f"[finished] status={p.get('status')} turns={p.get('turns')}\n"
            f"OUTPUT: {_clip(str(p.get('output', '')), 2000)}"
        )
    if t == "error":
        return f"[error] {_clip(str(p.get('message', '')), 1500)}"
    return f"[{t}] {_clip(json.dumps(p, ensure_ascii=False), 500)}"


def condense_trace(events: list[dict], max_chars: int = 16000) -> str:
    blocks = [_render_event(e if isinstance(e, dict) else e.model_dump()) for e in events]
    full = "\n".join(blocks)
    if len(full) <= max_chars:
        return full
    head_budget, head = max_chars * 3 // 10, []
    used = 0
    for b in blocks:
        if used + len(b) + 1 > head_budget:
            break
        head.append(b)
        used += len(b) + 1
    tail, tail_used = [], 0
    for b in reversed(blocks[len(head) :]):
        if used + tail_used + len(b) + 1 > max_chars - 60:
            break
        tail.append(b)
        tail_used += len(b) + 1
    tail.reverse()
    omitted = len(blocks) - len(head) - len(tail)
    out = "\n".join([*head, f"...[{omitted} steps omitted]...", *tail])
    return out[:max_chars]


# ---------------------------------------------------------------- optimizer

OPTIMIZER_SYSTEM = """You improve an AI agent by maintaining its list of LESSONS: short standing rules appended \
to its system prompt for every future task. You are shown runs where the agent failed (an automated check \
rejected the result) or where a user gave negative feedback. Your job is to change the lessons so the agent \
avoids these mistakes on FUTURE, DIFFERENT tasks.

How to work:
1. For each run, diagnose the root cause from the trace: what did the agent do or skip that led to the failure? \
Look past the symptom (e.g. "the test failed") to the behavior (e.g. "it edited code without running the tests").
2. Group runs that share a cause. Write one GENERAL rule per cause that would have prevented it and still makes \
sense for unrelated tasks in the same domain.
3. Each lesson is a single imperative sentence (or two), concrete and checkable: say what to do and when \
("Before finishing, run the project's test and lint commands and fix every failure."), not vague advice \
("Be careful."). Max 300 characters.
4. Prefer few, precise lessons. Edit an existing lesson rather than adding a near-duplicate; remove lessons \
that are wrong, redundant, or contradicted by the evidence. Do not restate what the system prompt already says \
unless the agent demonstrably ignored it, in which case make the rule more specific. Keep the total at 15 or fewer.
5. Every add or edit must cite evidence_run_ids: the run_id values (from the runs below) that motivate it. \
Never cite ids that are not listed.
6. Never mention specific task names, test names, file contents or expected outputs from these runs, and never \
copy the checker's wording. Lessons must generalize; a lesson that only fixes one known task is overfitting \
and will be rejected.
7. If no general rule is justified, return an empty list.

The system prompt holds the AUTHOR'S INSTRUCTIONS. They are authoritative, even when they look wrong to you, and \
only the author may change them:
8. Lessons may only fill gaps the instructions do not cover. A lesson must never contradict, override, weaken, \
carve an exception out of, or tell the agent to ignore or work around any instruction, and must never mention \
disobeying instructions ("even if an instruction says", "regardless of the instructions" and the like).
9. If a failure or feedback stems from the instructions themselves (the agent did what an instruction told it \
to, and that is what the check or the user rejected), do not write a lesson for it. Report it in \
"instruction_conflicts" instead: quote the instruction verbatim, state the problem the evidence shows, suggest \
how the author could change the instruction, and cite the run_ids. These go to the author, never to the agent.

Respond with ONLY a JSON object, no prose and no code fences:
{"lessons": [
  {"op": "add", "text": "<new lesson>", "evidence_run_ids": ["r_..."], "rationale": "<root cause it addresses>"},
  {"op": "edit", "old_text": "<existing lesson, verbatim>", "text": "<replacement>", "evidence_run_ids": ["r_..."], "rationale": "..."},
  {"op": "remove", "text": "<existing lesson, verbatim>", "evidence_run_ids": [], "rationale": "..."}
],
 "instruction_conflicts": [
  {"instruction_quote": "<instruction, verbatim from the system prompt>", "problem": "<what the evidence shows>", \
"suggested_change": "<how the author could change it>", "evidence_run_ids": ["r_..."]}
]}"""

CONFLICT_CHECK_SYSTEM = """You check proposed LESSONS for an AI agent against its AUTHOR'S INSTRUCTIONS. Lessons are \
appended after the instructions in the agent's system prompt, so a lesson that contradicts an instruction would \
silently override the author. The instructions are authoritative even when they look wrong; do not judge them.

For each lesson decide whether it contradicts an instruction: following the lesson would mean not following an \
instruction, or the lesson overrides, weakens, carves an exception out of, or tells the agent to ignore or work \
around an instruction. A lesson that only adds behavior the instructions do not address does not contradict them, \
and neither does one that restates or sharpens an instruction.

Respond with ONLY a JSON object, no prose and no code fences, with one entry per lesson in order:
{"checks": [{"index": 1, "contradicts": true or false, "instruction_quote": "<the contradicted instruction, \
verbatim, or empty>", "problem": "<one sentence, or empty>"}]}"""


def _item_trace(item: dict) -> str:
    if item.get("trace"):
        return _clip(str(item["trace"]), 8000)
    return condense_trace(item.get("events") or [], max_chars=8000)


def _optimizer_prompt(version: AgentVersion, failures: list[dict], feedback: list[dict]) -> str:
    lessons = "\n".join(f"{i}. {t}" for i, t in enumerate(version.lessons, 1)) or "(none yet)"
    parts = [
        f"<system_prompt>\n{version.system_prompt}\n</system_prompt>",
        f"<current_lessons>\n{lessons}\n</current_lessons>",
    ]
    for f in failures:
        parts.append(
            f'<failed_run run_id="{f.get("run_id", "")}">\n'
            f"<input>\n{_clip(str(f.get('input', '')), 3000)}\n</input>\n"
            f"<final_output>\n{_clip(str(f.get('output', '')), 2000)}\n</final_output>\n"
            f"<check_result>\n{_clip(str(f.get('reason', '')), 1000)}\n</check_result>\n"
            f"<trace>\n{_item_trace(f)}\n</trace>\n</failed_run>"
        )
    for fb in feedback:
        parts.append(
            f'<negative_feedback run_id="{fb.get("run_id", "")}">\n'
            f"<input>\n{_clip(str(fb.get('input', '')), 3000)}\n</input>\n"
            f"<final_output>\n{_clip(str(fb.get('output', '')), 2000)}\n</final_output>\n"
            f"<user_comment>\n{_clip(str(fb.get('comment', '') or '(no comment)'), 1000)}\n</user_comment>\n"
            f"<trace>\n{_item_trace(fb)}\n</trace>\n</negative_feedback>"
        )
    parts.append("Diagnose the root causes and return the lesson edits as the JSON object only.")
    return "\n\n".join(parts)


def _parse_edits(text: str) -> list[LessonEdit] | None:
    obj = _first_json_object(text)
    if obj is None or not isinstance(obj.get("lessons"), list):
        return None
    edits = []
    for raw in obj["lessons"]:
        try:
            edits.append(LessonEdit.model_validate(raw))
        except ValidationError:
            continue
    return edits


def _valid_edit(e: LessonEdit, run_ids: set[str], cases: list[dict]) -> bool:
    if any(r not in run_ids for r in e.evidence_run_ids):
        return False
    if e.op == "remove":
        return bool(e.text.strip())
    text = e.text.strip()
    return bool(text and e.evidence_run_ids) and len(text) <= MAX_LESSON_CHARS and not _leaks(text, cases)


def _parse_conflicts(text: str, run_ids: set[str]) -> list[InstructionConflict]:
    """The optimizer's `instruction_conflicts`, keeping entries with a quote and a problem; unknown run ids dropped."""
    obj = _first_json_object(text) or {}
    raw = obj.get("instruction_conflicts")
    out: list[InstructionConflict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("instruction_quote") or "").strip()
        problem = str(item.get("problem") or "").strip()
        if not quote or not problem:
            continue
        ids = item.get("evidence_run_ids")
        out.append(InstructionConflict(
            instruction_quote=quote[:MAX_CONFLICT_CHARS], problem=problem[:MAX_CONFLICT_CHARS],
            suggested_change=str(item.get("suggested_change") or "").strip()[:MAX_CONFLICT_CHARS],
            evidence_run_ids=[str(r) for r in ids if str(r) in run_ids] if isinstance(ids, list) else []))
    return out


# A lesson that talks about instructions as something to disobey is an override, whatever it says otherwise.
# Mentions of untrusted instructions found in content ("ignore instructions embedded in web pages") are allowed.
_INSTR = r"(?:instructions?|system\s+prompt|author'?s?\s+(?:rules?|wishes))"
_OVERRIDE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        rf"\beven\s+(?:if|when|though)\s+(?:an?\s+|the\s+|any\s+|your\s+|its\s+|some\s+)?(?:author'?s?\s+|system\s+)?"
        rf"{_INSTR}\s+(?:says?|tells?|asks?|requires?|demands?|directs?|wants?|states?|is|are)\b",
        r"\beven\s+(?:if|when|though)\s+(?:you\s+are\s+|you're\s+|it\s+is\s+)?(?:told|instructed|asked|directed)\b",
        rf"\b(?:ignore|disregard|disobey|overrid\w*|overrul\w*|bypass\w*|set\s+aside|deviate\s+from)\s+"
        rf"(?:the\s+|your\s+|its\s+|these\s+|those\s+|any\s+|an\s+|a\s+|all\s+|such\s+)?"
        rf"(?:author'?s?\s+|system\s+|original\s+|given\s+|stated\s+|conflicting\s+)?{_INSTR}\b"
        rf"(?!\s+(?:in|from|inside|within|embedded|found|contained|injected)\b)",
        rf"\b(?:regardless\s+of|despite|contrary\s+to|in\s+spite\s+of|notwithstanding|instead\s+of\s+following)"
        rf"\b[^.;]{{0,30}}\b{_INSTR}",
        rf"\b(?:takes?|taking)\s+precedence\s+over\b[^.;]{{0,30}}\b{_INSTR}",
    )
]
_OVERRIDE_WORD = re.compile(r"\b(?:overrid\w*|overrul\w*|supersed\w*)\b", re.IGNORECASE)
_MENTIONS_INSTRUCTIONS = re.compile(rf"\b{_INSTR}\b", re.IGNORECASE)


def overrides_instructions(text: str) -> bool:
    """True if a lesson references the instructions as something to disobey (deterministic check)."""
    if any(p.search(text) for p in _OVERRIDE_PATTERNS):
        return True
    return bool(_OVERRIDE_WORD.search(text) and _MENTIONS_INSTRUCTIONS.search(text))


def _conflict_check_prompt(instructions: str, lessons: list[str]) -> str:
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(lessons, 1))
    return (f"<instructions>\n{_clip(instructions, 12000)}\n</instructions>\n\n"
            f"<proposed_lessons>\n{numbered}\n</proposed_lessons>\n\n"
            "Check each lesson against the instructions. Reply with the JSON object only.")


async def check_instruction_conflicts(instructions: str, lessons: list[str],
                                      grader: Provider) -> list[dict[str, Any] | None]:
    """One structured grader call: per lesson {contradicts, instruction_quote, problem}, None where unknown.

    Fails open (all None) when the reply is unusable: the deterministic pattern check still applies, and a broken
    self-check should not stop an improvement cycle.
    """
    if not lessons or not instructions.strip():
        return [None] * len(lessons)
    try:
        resp = await grader.complete(CONFLICT_CHECK_SYSTEM,
                                     [Message(role="user", text=_conflict_check_prompt(instructions, lessons))],
                                     [], temperature=0)
    except Exception:  # noqa: BLE001
        log.warning("instruction conflict self-check failed", exc_info=True)
        return [None] * len(lessons)
    obj = _first_json_object(resp.text) or {}
    checks = obj.get("checks")
    out: list[dict[str, Any] | None] = [None] * len(lessons)
    for pos, c in enumerate(checks if isinstance(checks, list) else []):
        if not isinstance(c, dict) or not isinstance(c.get("contradicts"), bool):
            continue
        idx = c.get("index")
        i = idx - 1 if isinstance(idx, int) and 1 <= idx <= len(lessons) else pos
        if 0 <= i < len(lessons) and out[i] is None:
            out[i] = {"contradicts": c["contradicts"],
                      "instruction_quote": str(c.get("instruction_quote") or "").strip()[:MAX_CONFLICT_CHARS],
                      "problem": str(c.get("problem") or "").strip()[:MAX_CONFLICT_CHARS]}
    return out


def _conflict_from_lesson(e: LessonEdit, check: dict[str, Any] | None) -> InstructionConflict:
    quote = (check or {}).get("instruction_quote", "")
    problem = (check or {}).get("problem") or (
        "What the evaluations and feedback show conflicts with your instructions. The optimizer proposed a lesson "
        "that would override them, so it was not added.")
    return InstructionConflict(
        instruction_quote=quote, problem=problem,
        suggested_change=("If the behavior below is what you want, change the instruction yourself: "
                          f"{e.text}")[:MAX_CONFLICT_CHARS],
        evidence_run_ids=list(e.evidence_run_ids), rejected_lesson=e.text)


def _merge_conflicts(conflicts: list[InstructionConflict]) -> list[InstructionConflict]:
    """One entry per quoted instruction (evidence merged); unquoted entries are kept as they are."""
    out: list[InstructionConflict] = []
    by_quote: dict[str, InstructionConflict] = {}
    for c in conflicts:
        key = " ".join(c.instruction_quote.lower().split())
        if key and key in by_quote:
            kept = by_quote[key]
            kept.evidence_run_ids = list(dict.fromkeys([*kept.evidence_run_ids, *c.evidence_run_ids]))
            kept.rejected_lesson = kept.rejected_lesson or c.rejected_lesson
            continue
        if key:
            by_quote[key] = c
        out.append(c)
    return out[:MAX_CONFLICTS]


async def propose(
    version: AgentVersion,
    failures: list[dict],
    feedback: list[dict],
    grader: Provider,
    cases: list[dict],
) -> Proposal:
    """Optimizer call plus validation: validated lesson edits and the instruction conflicts for the author.

    Lessons never override the author's instructions. A lesson is dropped when it fails the evidence, length or leak
    checks; one that references the instructions as something to disobey (`overrides_instructions`) or that the
    grader self-check (`check_instruction_conflicts`) finds contradicting an instruction is dropped and reported as
    an instruction conflict instead.
    """
    if not failures and not feedback:
        return Proposal()
    messages = [Message(role="user", text=_optimizer_prompt(version, failures, feedback))]
    resp = await grader.complete(OPTIMIZER_SYSTEM, messages, [], temperature=0)
    edits = _parse_edits(resp.text)
    if edits is None:
        messages += [
            Message(role="assistant", text=resp.text),
            Message(role="user", text='That was not valid JSON. Reply with only {"lessons": [...], '
                                      '"instruction_conflicts": [...]}.'),
        ]
        resp = await grader.complete(OPTIMIZER_SYSTEM, messages, [], temperature=0)
        edits = _parse_edits(resp.text) or []
    run_ids = {str(x["run_id"]) for x in [*failures, *feedback] if x.get("run_id")}
    conflicts = _parse_conflicts(resp.text, run_ids)
    for e in edits:
        e.text = e.text.strip()
    edits = [e for e in edits if _valid_edit(e, run_ids, cases)]

    # Every add or edit goes through the self-check (pattern-rejected ones too, so their conflict gets a quote).
    new_text = [e for e in edits if e.op != "remove"]
    checks = await check_instruction_conflicts(version.system_prompt, [e.text for e in new_text], grader)
    rejected: dict[int, dict[str, Any] | None] = {}
    for e, check in zip(new_text, checks, strict=True):
        if overrides_instructions(e.text) or (check is not None and check["contradicts"]):
            rejected[id(e)] = check
    for e in new_text:
        if id(e) in rejected:
            conflicts.append(_conflict_from_lesson(e, rejected[id(e)]))
    kept = [e for e in edits if id(e) not in rejected][:MAX_LESSONS]
    return Proposal(lessons=kept, instruction_conflicts=_merge_conflicts(conflicts))


async def propose_lessons(
    version: AgentVersion,
    failures: list[dict],
    feedback: list[dict],
    grader: Provider,
    cases: list[dict],
) -> list[LessonEdit]:
    """The validated lesson edits of `propose` (instruction conflicts dropped)."""
    return (await propose(version, failures, feedback, grader, cases)).lessons


def apply_lesson_edits(lessons: list[str], edits: list[LessonEdit]) -> list[str]:
    out = list(lessons)

    def find(text: str | None) -> int | None:
        if not text:
            return None
        if text in out:
            return out.index(text)
        low = text.strip().lower()
        return next((i for i, t in enumerate(out) if t.strip().lower() == low), None)

    for e in edits:
        if e.op == "add":
            if e.text not in out:
                out.append(e.text)
        elif e.op == "edit":
            i = find(e.old_text)
            if i is not None:
                out[i] = e.text
            elif e.text not in out:
                out.append(e.text)
        elif e.op == "remove":
            i = find(e.text)
            if i is not None:
                out.pop(i)
    # dedupe after edits (an edit may collide with an existing lesson), keeping first occurrence
    out = list(dict.fromkeys(out))
    return out[-MAX_LESSONS:]


# ---------------------------------------------------------------- repeated runs


def majority(passes: int, k: int) -> bool:
    """A case passes iff a strict majority of its k runs pass (for even k a tie fails: 2/4 is not > half)."""
    return k > 0 and 2 * passes > k


def summarize_runs(runs: list[dict]) -> dict:
    """Per-case verdict from its repeated runs (each {"passed", "reason", "run_id"}, in run order).

    The representative run is the first failing run when the verdict is fail, else the first passing run,
    so its trace and reason explain the verdict.
    """
    passes = sum(1 for r in runs if r.get("passed"))
    passed = majority(passes, len(runs))
    rep = next((r for r in runs if bool(r.get("passed")) == passed), runs[0] if runs else {})
    return {"passed": passed, "reason": rep.get("reason", ""), "run_id": rep.get("run_id", ""),
            "passes": passes, "runs": len(runs)}


def apply_lesson_edits_meta(lessons: list[str], meta: dict | None, edits: list[LessonEdit], version_n: int, *,
                            origin: str = "optimizer", at: float | None = None) -> tuple[list[str], dict]:
    """`apply_lesson_edits` plus the provenance of the resulting lessons (see forge.provenance)."""
    from forge.provenance import lesson_provenance

    new = apply_lesson_edits(lessons, edits)
    return new, lesson_provenance(lessons, meta, new, edits, version_n, origin=origin, at=at)


# ---------------------------------------------------------------- promotion


def _result(side: dict[str, dict], case_id: str) -> dict:
    r = side.get(case_id)
    if r is None:
        return {"passed": False, "reason": "not run", "run_id": "", "passes": 0, "runs": 0}
    passed = bool(r.get("passed"))
    return {"passed": passed, "reason": r.get("reason", ""), "run_id": r.get("run_id", ""),
            "passes": r.get("passes", int(passed)), "runs": r.get("runs", 1)}


def compare(cases: list[dict], base: dict[str, dict], cand: dict[str, dict]) -> dict:
    """Promotion comparison on per-case verdicts (already majority-of-k when evals repeat runs)."""
    summary = {s: {sp: {"passed": 0, "total": 0} for sp in ("train", "holdout")} for s in ("base", "candidate")}
    rows, newly, regressions = [], [], []
    for c in cases:
        cid, split = c["id"], c.get("split", "train")
        b, k = _result(base, cid), _result(cand, cid)
        for side, r in (("base", b), ("candidate", k)):
            bucket = summary[side].setdefault(split, {"passed": 0, "total": 0})
            bucket["total"] += 1
            bucket["passed"] += int(r["passed"])
        if k["passed"] and not b["passed"]:
            newly.append(cid)
        if b["passed"] and not k["passed"]:
            regressions.append(cid)
        rows.append({"case_id": cid, "name": c.get("name", ""), "split": split, "base": b, "candidate": k})
    base_total = sum(v["passed"] for v in summary["base"].values())
    cand_total = sum(v["passed"] for v in summary["candidate"].values())
    return {
        "base": summary["base"],
        "candidate": summary["candidate"],
        "cases": rows,
        "newly_passing": newly,
        "regressions": regressions,
        "eligible": cand_total > base_total and not regressions,
    }


def is_eligible(cases: list[dict], base: dict[str, dict], cand: dict[str, dict]) -> bool:
    return compare(cases, base, cand)["eligible"]
