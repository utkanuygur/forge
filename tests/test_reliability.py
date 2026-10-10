"""Reliability: stop reasons, prompt caching, and majority-of-k eval verdicts (FR10, FR12, NFR5)."""

from __future__ import annotations

import copy
import json
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types import Message as SDKMessage

from forge import db
from forge.improve import compare, majority, summarize_runs
from forge.loop import TOOL_SPECS, TRUNCATED_CALL, run_agent
from forge.providers import AnthropicProvider, FakeProvider, OpenAICompatProvider, ScenarioProvider
from forge.types import AgentVersion, Event, Message, ModelResponse, ToolCall

# ---------------------------------------------------------------- stubbed SDKs


def sdk_message(content: list[dict], stop_reason: str = "end_turn", stop_details: dict | None = None,
                cache_read: int | None = 0, cache_write: int | None = 0) -> SDKMessage:
    return SDKMessage.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
        "stop_reason": stop_reason, "stop_sequence": None, "stop_details": stop_details,
        "usage": {"input_tokens": 12, "output_tokens": 7, "cache_read_input_tokens": cache_read,
                  "cache_creation_input_tokens": cache_write},
    })


class StubAnthropic:
    def __init__(self, *responses: SDKMessage) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs: Any) -> SDKMessage:
        self.requests.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


TOOLS = [TOOL_SPECS["bash"], TOOL_SPECS["write_file"]]


async def test_anthropic_refusal_stop_details_parsed():
    stub = StubAnthropic(sdk_message([{"type": "text", "text": "I can't help with that."}], "refusal",
                                     {"type": "refusal", "category": "cyber", "explanation": "Declined."}))
    resp = await AnthropicProvider("claude-opus-5-5", client=stub).complete("sys", [Message(role="user", text="x")], [])
    assert resp.stop_reason == "refusal" and resp.stop_detail == "cyber"

    stub = StubAnthropic(sdk_message([], "refusal", {"type": "refusal", "category": None, "explanation": "Policy."}))
    resp = await AnthropicProvider("m", client=stub).complete("sys", [Message(role="user", text="x")], [])
    assert resp.stop_detail == "Policy."  # no category: the explanation is the detail

    stub = StubAnthropic(sdk_message([{"type": "text", "text": "cut"}], "max_tokens"))
    resp = await AnthropicProvider("m", client=stub).complete("sys", [Message(role="user", text="x")], [])
    assert resp.stop_reason == "max_tokens" and resp.stop_detail is None


async def test_anthropic_cache_breakpoints_and_usage():
    """Breakpoint on the system block (caches tools + system) and on the last message block; at most 4."""
    stub = StubAnthropic(
        sdk_message([{"type": "tool_use", "id": "t1", "name": "bash", "input": {"cmd": "ls"}}], "tool_use",
                    cache_read=0, cache_write=900),
        sdk_message([{"type": "text", "text": "done"}], cache_read=900, cache_write=40),
    )
    p = AnthropicProvider("claude-opus-5-5", client=stub)
    history = [Message(role="user", text="list files")]
    first = await p.complete("You are helpful.", history, TOOLS)
    assert (first.cache_read_tokens, first.cache_write_tokens, first.tokens_in) == (0, 900, 12)
    history += [Message(role="assistant", tool_calls=first.tool_calls),
                Message(role="tool", tool_call_id="t1", text="a.txt")]
    second = await p.complete("You are helpful.", history, TOOLS)
    assert (second.cache_read_tokens, second.cache_write_tokens) == (900, 40)

    r1, r2 = stub.requests
    for req in (r1, r2):
        assert req["system"] == [{"type": "text", "text": "You are helpful.", "cache_control": {"type": "ephemeral"}}]
        marked = [b for m in req["messages"] if isinstance(m["content"], list) for b in m["content"]
                  if "cache_control" in b]
        assert marked == [req["messages"][-1]["content"][-1]]  # exactly one, on the newest block
        assert 1 + len(marked) <= 4
    assert r1["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "list files", "cache_control": {"type": "ephemeral"}}]}]
    assert r2["messages"][-1]["content"][-1]["type"] == "tool_result"
    # byte-stable prefix: tools and system identical across turns, prior turn identical once markers are stripped
    assert json.dumps(r1["tools"]) == json.dumps(r2["tools"]) and r1["system"] == r2["system"]
    assert [t["name"] for t in r2["tools"]] == ["bash", "write_file"]
    strip = lambda ms: [{**m, "content": [{k: v for k, v in b.items() if k != "cache_control"}  # noqa: E731
                                          for b in m["content"]]} for m in ms]
    assert strip(r2["messages"][:1]) == strip(r1["messages"])
    # the cached raw assistant turn was not mutated by the breakpoint
    assert all("cache_control" not in b for raw in p._raw.values() for b in raw)


async def test_anthropic_one_shot_calls_cache_only_the_system_prompt():
    """Judge and optimizer calls (no tools) have no next turn, so the conversation is not marked."""
    stub = StubAnthropic(sdk_message([{"type": "text", "text": "{}"}]))
    await AnthropicProvider("m", client=stub).complete("judge", [Message(role="user", text="grade")], [])
    req = stub.requests[0]
    assert req["messages"] == [{"role": "user", "content": [{"type": "text", "text": "grade"}]}]
    assert "tools" not in req
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}


async def test_openai_finish_reason_normalized():
    def completion(finish: str) -> Any:
        msg = SimpleNamespace(content="partial", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)],
                               usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4))

    for finish, want in [("length", "max_tokens"), ("stop", "end_turn"), ("tool_calls", "tool_use"),
                         ("content_filter", "refusal")]:
        async def create(**_: Any) -> Any:
            return completion(finish)

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        resp = await OpenAICompatProvider("http://x/v1", "m", client=client).complete(
            "s", [Message(role="user", text="x")], [])
        assert resp.stop_reason == want, finish


# ---------------------------------------------------------------- loop


class RecordingExecutor:
    def __init__(self) -> None:
        self.cmds: list[str] = []
        self.writes: list[str] = []

    async def exec(self, cmd: str, timeout: int = 120):
        from forge.types import ExecResult

        self.cmds.append(cmd)
        return ExecResult(exit_code=0, stdout="ok", stderr="")

    async def write(self, path: str, content: str) -> None:
        self.writes.append(path)


async def run(responses: list[ModelResponse]) -> tuple[Any, list[Event], RecordingExecutor]:
    events: list[Event] = []

    async def emit(e: Event) -> None:
        events.append(e)

    ex = RecordingExecutor()
    v = AgentVersion(id="v", agent_id="a", n=1, system_prompt="s", model="fake:x")
    result = await run_agent(v, "do it", run_id="r", provider=FakeProvider(responses), executor=ex, emit=emit)
    return result, events, ex


def bash(i: int, cmd: str) -> ToolCall:
    return ToolCall(id=f"c{i}", name="bash", args={"cmd": cmd})


async def test_refusal_fails_run_without_running_its_tool_calls():
    result, events, ex = await run([
        ModelResponse(tool_calls=[bash(0, "echo first")], stop_reason="tool_use"),
        ModelResponse(text="No.", tool_calls=[bash(1, "rm -rf /")], stop_reason="refusal", stop_detail="cyber"),
    ])
    assert result.status == "failed" and result.error == "Model declined: cyber"
    assert ex.cmds == ["echo first"]
    assert [e.type for e in events] == ["run_started", "tool_call", "tool_result", "error", "run_finished"]
    assert events[-2].payload == {"message": "Model declined: cyber"}
    assert events[-1].payload["status"] == "failed"


async def test_refusal_without_detail():
    result, _, _ = await run([ModelResponse(stop_reason="refusal")])
    assert result.status == "failed" and result.error == "Model declined: no reason given"


async def test_max_tokens_without_tool_calls_is_not_a_final_answer():
    result, events, _ = await run([ModelResponse(text="The answer is", stop_reason="max_tokens")])
    assert result.status == "failed" and "max_tokens" in (result.error or "")
    assert [e.type for e in events] == ["run_started", "assistant", "error", "run_finished"]
    assert events[-1].payload["status"] == "failed" and events[-1].payload["output"] == "The answer is"


async def test_max_tokens_with_tool_calls_skips_only_the_possibly_truncated_last_call():
    result, events, ex = await run([
        ModelResponse(tool_calls=[bash(0, "echo a"), bash(1, "echo b")], stop_reason="max_tokens"),
        ModelResponse(text="Finished.", stop_reason="end_turn"),
    ])
    assert result.status == "succeeded" and ex.cmds == ["echo a"]
    outs = [e.payload for e in events if e.type == "tool_result"]
    assert outs[1] == {"id": "c1", "name": "bash", "output": TRUNCATED_CALL, "is_error": True}


async def test_cache_tokens_summed_into_run_finished():
    result, events, _ = await run([
        ModelResponse(tool_calls=[bash(0, "true")], cache_write_tokens=800),
        ModelResponse(text="ok", cache_read_tokens=800, cache_write_tokens=30),
    ])
    assert (result.cache_read_tokens, result.cache_write_tokens) == (800, 830)
    p = events[-1].payload
    assert (p["cache_read_tokens"], p["cache_write_tokens"]) == (800, 830)


# ---------------------------------------------------------------- majority of k


def test_majority_rule_odd_and_even_k():
    assert [majority(p, 3) for p in range(4)] == [False, False, True, True]  # odd k: no ties possible
    assert [majority(p, 4) for p in range(5)] == [False, False, False, True, True]  # 2/4 is not > half
    assert [majority(p, 2) for p in range(3)] == [False, False, True]
    assert majority(1, 1) and not majority(0, 1) and not majority(0, 0)


def test_summarize_runs_picks_representative_run():
    runs = [{"passed": True, "reason": "ok", "run_id": "r_a"}, {"passed": False, "reason": "bad", "run_id": "r_b"},
            {"passed": True, "reason": "ok2", "run_id": "r_c"}]
    assert summarize_runs(runs) == {"passed": True, "reason": "ok", "run_id": "r_a", "passes": 2, "runs": 3}
    fail = summarize_runs(runs[:2])  # 1/2: tie fails
    assert fail == {"passed": False, "reason": "bad", "run_id": "r_b", "passes": 1, "runs": 2}


def test_compare_uses_majority_verdicts_and_carries_counts():
    cases = [{"id": "t1", "name": "t1", "split": "train"}, {"id": "t2", "name": "t2", "split": "train"}]
    base = {"t1": {"passed": True, "reason": "", "run_id": "r1", "passes": 2, "runs": 3},
            "t2": {"passed": False, "reason": "", "run_id": "r2", "passes": 1, "runs": 3}}
    cand = {"t1": {"passed": True, "reason": "", "run_id": "r3", "passes": 3, "runs": 3},
            "t2": {"passed": True, "reason": "", "run_id": "r4", "passes": 2, "runs": 3}}
    res = compare(cases, base, cand)
    assert res["eligible"] and res["newly_passing"] == ["t2"]
    assert (res["cases"][1]["base"]["passes"], res["cases"][1]["candidate"]["passes"]) == (1, 2)
    missing = compare(cases, {}, {})["cases"][0]["base"]
    assert (missing["passes"], missing["runs"]) == (0, 0)


async def test_fake_flaky_alternates_per_input_in_any_order():
    p = ScenarioProvider("flaky")
    user = [Message(role="user", text="flaky-unit-test-input")]
    first = [await p.complete("sys", user, []) for _ in range(3)]
    assert [bool(r.tool_calls) for r in first] == [True, False, True]
    other = await p.complete("sys", [Message(role="user", text="another-input")], [])
    assert other.tool_calls  # independent counter per input
    follow = await p.complete("sys", user + [Message(role="assistant", tool_calls=first[0].tool_calls),
                                             Message(role="tool", tool_call_id="call_0", text="ok")], [])
    assert follow.text == "Done." and not follow.tool_calls


# ---------------------------------------------------------------- schema migration


def test_migration_adds_columns_and_backfills_single_run_results(tmp_path, monkeypatch):
    """Existing databases gain the new columns; old one-run verdicts become run 0 with passes/runs filled."""
    data = tmp_path / "data"
    data.mkdir()
    schema = db.SCHEMA[:db.SCHEMA.index("-- One row per repeated eval run")]
    schema = "\n".join(line for line in schema.splitlines()
                       if not any(c in line for c in ("eval_repeats", "cache_read_tokens", "cache_write_tokens",
                                                      "passes      INTEGER", "runs        INTEGER")))
    schema += db.SCHEMA[db.SCHEMA.index("CREATE TABLE IF NOT EXISTS improvements"):]
    old = sqlite3.connect(data / "forge.db")
    old.executescript(schema + """
        INSERT INTO agents VALUES ('ag_1', 'old', 'Old', '', 'v_1', 0, 'h', 's', 1.0);
        INSERT INTO agent_versions (id, agent_id, n, system_prompt, model, tools, created_at)
          VALUES ('v_1', 'ag_1', 1, 'p', 'fake:echo', '[]', 1.0);
        INSERT INTO eval_cases VALUES ('ec_1', 'ag_1', 'c', 'i', '{}', 'shell', 'true', 'train', 1.0);
        INSERT INTO runs (id, agent_id, version_id, source, input, status, created_at)
          VALUES ('r_1', 'ag_1', 'v_1', 'eval', 'i', 'succeeded', 1.0);
        INSERT INTO eval_results VALUES ('v_1', 'ec_1', 'r_1', 1, 'passed', 1.0);
    """)
    assert "passes" not in {r[1] for r in old.execute("PRAGMA table_info(eval_results)")}
    old.close()
    monkeypatch.setenv("FORGE_DATA_DIR", str(data))
    db._initialized.clear()
    [row] = db.list_eval_results("v_1")
    assert (row["passes"], row["runs"], row["passed"]) == (1, 1, True)
    [run0] = db.list_eval_run_results("v_1", "ec_1")
    assert (run0["idx"], run0["run_id"], run0["passed"]) == (0, "r_1", True)
    agent, _ = db.create_agent("a", "A")
    assert agent["eval_repeats"] is None
    assert db.update_agent(agent["id"], eval_repeats=5)["eval_repeats"] == 5
    db._initialized.clear()
    db.list_eval_results("v_1")  # migrating again is a no-op
    assert len(db.list_eval_run_results("v_1")) == 1


@pytest.fixture(autouse=True)
def _reset_db_init():
    yield
    db._initialized.clear()
