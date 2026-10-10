from __future__ import annotations

import json

from forge.improve import (
    MAX_LESSONS,
    LessonEdit,
    apply_lesson_edits,
    check_case,
    compare,
    condense_trace,
    is_eligible,
    judge,
    propose_lessons,
)
from forge.types import AgentVersion, ExecResult, Message, ModelResponse, ToolSpec


class ScriptedProvider:
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.calls: list[dict] = []

    async def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec], temperature: float | None = None
    ) -> ModelResponse:
        self.calls.append({"system": system, "messages": messages, "tools": tools, "temperature": temperature})
        return ModelResponse(text=self.texts.pop(0))


class FakeExecutor:
    def __init__(self, results: dict[str, ExecResult]):
        self.results = results
        self.cmds: list[tuple[str, int]] = []

    async def exec(self, cmd: str, timeout: int = 120) -> ExecResult:
        self.cmds.append((cmd, timeout))
        return self.results.get(cmd, ExecResult(exit_code=1, stdout="", stderr="unknown"))


def case(cid: str, name: str = "", split: str = "train", checker_type: str = "shell", checker: str = "true") -> dict:
    return {"id": cid, "name": name or cid, "input": "do it", "seed_files": {},
            "checker_type": checker_type, "checker": checker, "split": split}


VERSION = AgentVersion(id="v_1", agent_id="ag_1", n=1, system_prompt="You maintain repos.",
                       lessons=["Old lesson."], model="anthropic:x")

# ---------------------------------------------------------------- check_case / judge


async def test_shell_pass_and_fail():
    ex = FakeExecutor({
        "make check": ExecResult(exit_code=0, stdout="ok", stderr=""),
        "make bad": ExecResult(exit_code=2, stdout="x" * 2000, stderr="FAILED test_refund"),
    })
    assert await check_case(case("a", checker="make check"), ex, "", ScriptedProvider()) == (True, "passed")
    ok, reason = await check_case(case("b", checker="make bad"), ex, "", ScriptedProvider())
    assert not ok and reason.endswith("FAILED test_refund") and len(reason) < 900
    assert ex.cmds[0] == ("make check", 300)


async def test_judge_case_uses_diff_and_parses_messy_json():
    diff_cmd = "git add -A && git diff --cached baseline"
    ex = FakeExecutor({diff_cmd: ExecResult(exit_code=0, stdout="+def refund(): ...", stderr="")})
    grader = ScriptedProvider('Sure! Here is my verdict:\n```json\n{"passed": true, "reason": "Endpoint added."}\n```\nBye.')
    ok, reason = await check_case(case("j", checker_type="judge", checker="Adds a refund endpoint"), ex, "done", grader)
    assert (ok, reason) == (True, "Endpoint added.")
    call = grader.calls[0]
    assert call["temperature"] == 0 and call["tools"] == []
    assert "+def refund()" in call["messages"][0].text and "Adds a refund endpoint" in call["messages"][0].text


async def test_judge_unparseable_and_brace_in_prose():
    ok, reason = await judge("r", "t", "o", "", ScriptedProvider("I think it passes."))
    assert not ok and reason.startswith("judge output unparseable")
    ok, reason = await judge("r", "t", "o", "", ScriptedProvider('Note {not json} then {"passed": false, "reason": "no tests"}'))
    assert (ok, reason) == (False, "no tests")


async def test_judge_truncates_long_diff():
    grader = ScriptedProvider('{"passed": false, "reason": "x"}')
    await judge("r", "t", "o", "d" * 100_000, grader)
    assert len(grader.calls[0]["messages"][0].text) < 30_000


# ---------------------------------------------------------------- condense_trace


def ev(t: str, **payload) -> dict:
    return {"run_id": "r_1", "seq": 0, "ts": 0.0, "type": t, "payload": payload}


def test_condense_trace_renders_and_truncates():
    events = [ev("run_started", version_id="v", model="m", input="FIRST_INPUT")]
    for i in range(200):
        events.append(ev("tool_call", id=str(i), name="bash", args={"cmd": f"ls step{i}"}))
        events.append(ev("tool_result", id=str(i), name="bash", output="HEAD" + "y" * 5000 + "TAIL", is_error=False))
    events.append(ev("run_finished", status="succeeded", output="LAST_OUTPUT", turns=200, tokens_in=0, tokens_out=0))

    short = condense_trace(events[:3])
    assert '[call bash] {"cmd": "ls step0"}' in short and "HEAD" in short and "TAIL" in short
    assert "truncated" in short and len(short) < 3000

    out = condense_trace(events, max_chars=16000)
    assert len(out) <= 16000
    assert "FIRST_INPUT" in out and "LAST_OUTPUT" in out and "steps omitted" in out
    assert "step199" in out and "step100" not in out


# ---------------------------------------------------------------- propose_lessons


FAILURES = [{"run_id": "r_a", "input": "add endpoint", "output": "done", "reason": "exit 1. lint failed",
             "events": [ev("tool_call", id="1", name="bash", args={"cmd": "echo hi"})]}]
FEEDBACK = [{"run_id": "r_b", "input": "fix bug", "output": "fixed", "comment": "you left prints", "events": []}]
CASES = [case("ec_1", name="add-refund-endpoint", checker="make check && ! grep -rn 'print(' src"),
         case("ec_2", name="summary", checker_type="judge",
              checker="The summary must mention the three largest customers by revenue")]


async def test_propose_lessons_validates():
    lessons = [
        {"op": "add", "text": "Run the test and lint commands before finishing.", "evidence_run_ids": ["r_a"]},
        {"op": "add", "text": "Bogus evidence.", "evidence_run_ids": ["r_zzz"]},
        {"op": "add", "text": "No evidence at all.", "evidence_run_ids": []},
        {"op": "add", "text": "For add-refund-endpoint, add a route.", "evidence_run_ids": ["r_a"]},
        {"op": "add", "text": "Always mention the three largest customers by revenue.", "evidence_run_ids": ["r_b"]},
        {"op": "add", "text": "z" * 301, "evidence_run_ids": ["r_a"]},
        {"op": "edit", "old_text": "Old lesson.", "text": "Remove debug prints before finishing.",
         "evidence_run_ids": ["r_b"]},
        {"op": "remove", "text": "Old lesson.", "evidence_run_ids": []},
        {"op": "explode", "text": "invalid op"},
    ]
    grader = ScriptedProvider("Here you go: " + json.dumps({"lessons": lessons}))
    edits = await propose_lessons(VERSION, FAILURES, FEEDBACK, grader, CASES)
    assert [e.text for e in edits] == [
        "Run the test and lint commands before finishing.",
        "Remove debug prints before finishing.",
        "Old lesson.",
    ]
    prompt = grader.calls[0]["messages"][0].text
    assert "r_a" in prompt and "you left prints" in prompt and "1. Old lesson." in prompt
    assert "add-refund-endpoint" not in prompt and grader.calls[0]["temperature"] == 0


async def test_propose_lessons_retries_once_and_caps():
    many = [{"op": "add", "text": f"Rule number {i}.", "evidence_run_ids": ["r_a"]} for i in range(30)]
    grader = ScriptedProvider("not json", json.dumps({"lessons": many}))
    edits = await propose_lessons(VERSION, FAILURES, [], grader, CASES)
    assert len(grader.calls) == 2 and len(edits) == MAX_LESSONS
    assert await propose_lessons(VERSION, FAILURES, [], ScriptedProvider("nope", "still nope"), CASES) == []


# ---------------------------------------------------------------- apply_lesson_edits


def test_apply_lesson_edits_ops():
    base = ["A rule.", "B rule.", "C rule."]
    out = apply_lesson_edits(base, [
        LessonEdit(op="add", text="D rule."),
        LessonEdit(op="add", text="A rule."),  # duplicate skipped
        LessonEdit(op="edit", old_text="b RULE.", text="B2 rule."),  # case-insensitive
        LessonEdit(op="edit", old_text="missing", text="E rule."),  # appended
        LessonEdit(op="remove", text="C rule."),
        LessonEdit(op="remove", text="not there"),
    ])
    assert out == ["A rule.", "B2 rule.", "D rule.", "E rule."]
    assert base == ["A rule.", "B rule.", "C rule."]


def test_apply_lesson_edits_cap_keeps_newest():
    old = [f"old {i}" for i in range(MAX_LESSONS)]
    out = apply_lesson_edits(old, [LessonEdit(op="add", text="new 1"), LessonEdit(op="add", text="new 2")])
    assert len(out) == MAX_LESSONS and out[-2:] == ["new 1", "new 2"] and "old 0" not in out


# ---------------------------------------------------------------- compare / is_eligible


def r(passed: bool, run_id: str = "r") -> dict:
    return {"passed": passed, "reason": "ok" if passed else "bad", "run_id": run_id}


CMP_CASES = [case("t1"), case("t2"), case("h1", split="holdout"), case("h2", split="holdout")]


def test_compare_improvement_eligible():
    base = {"t1": r(True), "t2": r(False), "h1": r(False)}  # h2 missing -> failed
    cand = {"t1": r(True), "t2": r(True), "h1": r(True), "h2": r(False)}
    res = compare(CMP_CASES, base, cand)
    assert res["base"] == {"train": {"passed": 1, "total": 2}, "holdout": {"passed": 0, "total": 2}}
    assert res["candidate"] == {"train": {"passed": 2, "total": 2}, "holdout": {"passed": 1, "total": 2}}
    assert res["newly_passing"] == ["t2", "h1"] and res["regressions"] == []
    assert res["eligible"] and is_eligible(CMP_CASES, base, cand)
    row = res["cases"][3]
    assert row["case_id"] == "h2" and row["split"] == "holdout" and row["base"]["passed"] is False


def test_regression_blocks_promotion():
    base = {"t1": r(True), "t2": r(False), "h1": r(False), "h2": r(False)}
    cand = {"t1": r(False), "t2": r(True), "h1": r(True), "h2": r(True)}
    res = compare(CMP_CASES, base, cand)
    assert res["regressions"] == ["t1"] and not res["eligible"]
    assert not is_eligible(CMP_CASES, base, cand)


def test_equal_score_not_eligible():
    base = {"t1": r(True), "t2": r(False), "h1": r(True), "h2": r(False)}
    assert not is_eligible(CMP_CASES, base, dict(base))
    assert not is_eligible(CMP_CASES, {}, {})
