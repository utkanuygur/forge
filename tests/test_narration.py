"""Narration: progress updates surface as `assistant` events before the turn's tool calls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from forge.executors import LocalExecutor
from forge.loop import build_system_prompt, run_agent
from forge.providers import (
    INTERRUPTED_NOTE,
    PROGRESS_UPDATES_BETA,
    AnthropicProvider,
    FakeProvider,
    ScenarioProvider,
    supports_progress_updates,
)
from forge.types import AgentVersion, Event, Message, ModelResponse, ToolCall, ToolSpec


def version(**kw) -> AgentVersion:
    return AgentVersion(id="v_t", agent_id="ag_t", n=1, system_prompt="You are a test agent.", model="fake:x", **kw)


@pytest.fixture
async def local(tmp_path):
    ex = LocalExecutor(root=str(tmp_path / "ws"))
    await ex.start({}, {})
    yield ex
    await ex.stop()


async def _run(responses, executor):
    events: list[Event] = []

    async def emit(e: Event) -> None:
        events.append(e)

    result = await run_agent(
        version(), "go", run_id="r_t", provider=FakeProvider(responses), executor=executor, emit=emit
    )
    return result, events


async def test_notes_emitted_as_progress_before_tool_calls(local):
    responses = [
        ModelResponse(
            notes=["I'll create the file.", "  "],
            text="Short remark.",
            tool_calls=[ToolCall(id="c1", name="write_file", args={"path": "a.txt", "content": "x\n"})],
        ),
        ModelResponse(text="All **done**."),
    ]
    result, events = await _run(responses, local)
    assert result.status == "succeeded"
    kinds = [(e.type, e.payload.get("kind"), e.payload.get("text")) for e in events if e.type != "run_started"]
    assert kinds[:3] == [
        ("assistant", "progress", "I'll create the file."),
        ("assistant", "progress", "Short remark."),
        ("tool_call", None, None),
    ]
    assert ("assistant", "final", "All **done**.") in kinds
    # Ordering is strict: every progress event of a turn precedes that turn's tool_call.
    seqs = {e.payload.get("text"): e.seq for e in events if e.type == "assistant"}
    call_seq = next(e.seq for e in events if e.type == "tool_call")
    assert seqs["I'll create the file."] < seqs["Short remark."] < call_seq


async def test_plain_text_before_tool_calls_is_progress(local):
    """Providers without notes (OpenAI-compatible) still narrate via plain text before tool calls."""
    responses = [
        ModelResponse(text="Running ls.", tool_calls=[ToolCall(id="c1", name="bash", args={"cmd": "true"})]),
        ModelResponse(text="Finished."),
    ]
    _, events = await _run(responses, local)
    assistant = [e.payload for e in events if e.type == "assistant"]
    assert assistant == [{"text": "Running ls.", "kind": "progress"}, {"text": "Finished.", "kind": "final"}]


async def test_scenario_hello_narrates():
    p = ScenarioProvider("hello")
    r = await p.complete("", [Message(role="user", text="x")], [])
    assert r.notes == ["I'll create hello.py."] and r.tool_calls[0].name == "write_file"


def test_system_prompt_environment_note():
    p = build_system_prompt(version())
    assert "Before each group of tool calls, say in one short sentence what you are about to do." in p
    assert "use read_file, write_file and edit_file" in p.lower()
    assert "not for editing files" in p


# ---------------------------------------------------------------- anthropic provider


class _Block(SimpleNamespace):
    def model_dump(self, exclude_none: bool = False):
        d = dict(self.__dict__)
        return {k: v for k, v in d.items() if v is not None} if exclude_none else d


class _Stub:
    """Stands in for anthropic.AsyncAnthropic: records requests on both the beta and GA paths."""

    def __init__(self, content):
        self.requests: list[tuple[str, dict]] = []
        self._content = content
        outer = self

        class _Msgs:
            def __init__(self, path):
                self.path = path

            async def create(self, **kw):
                outer.requests.append((self.path, kw))
                return SimpleNamespace(content=outer._content, usage=SimpleNamespace(input_tokens=7, output_tokens=3))

        self.messages = _Msgs("ga")
        self.beta = SimpleNamespace(messages=_Msgs("beta"))


CONTENT = [
    _Block(type="thinking", thinking="", signature="sig0"),  # hidden reasoning: empty under "updates"
    _Block(type="thinking", thinking="Reading the router to find where to add the endpoint.", signature="sig1"),
    _Block(type="tool_use", id="tu1", name="read_file", input={"path": "app.py"}),
    _Block(type="thinking", thinking=INTERRUPTED_NOTE, signature="sig2"),
]
TOOLS = [ToolSpec(name="read_file", description="d", parameters={"type": "object", "properties": {}})]


async def test_anthropic_requests_progress_updates_and_returns_notes():
    stub = _Stub(CONTENT)
    p = AnthropicProvider("claude-opus-5-5", client=stub)
    r = await p.complete("sys", [Message(role="user", text="hi")], TOOLS)
    path, kw = stub.requests[0]
    assert path == "beta"
    assert kw["thinking"] == {"type": "adaptive", "display": "updates"}
    assert kw["betas"] == [PROGRESS_UPDATES_BETA] == ["thinking-display-updates-2026-08-18"]
    assert r.notes == ["Reading the router to find where to add the endpoint."]
    assert r.text == "" and [c.id for c in r.tool_calls] == ["tu1"]

    # Thinking blocks (with signatures) are replayed unchanged on the next turn.
    history = [
        Message(role="user", text="hi"),
        Message(role="assistant", text=r.text, tool_calls=r.tool_calls),
        Message(role="tool", tool_call_id="tu1", text="ok"),
    ]
    await p.complete("sys", history, TOOLS)
    replay = stub.requests[1][1]["messages"][1]["content"]
    assert [b["type"] for b in replay] == ["thinking", "thinking", "tool_use", "thinking"]
    assert replay[1]["signature"] == "sig1" and replay[1]["thinking"].startswith("Reading the router")


async def test_anthropic_older_models_use_plain_request():
    stub = _Stub([_Block(type="text", text="hello", citations=None)])
    p = AnthropicProvider("claude-haiku-4-5", client=stub)
    r = await p.complete("sys", [Message(role="user", text="hi")], [])
    path, kw = stub.requests[0]
    assert path == "ga" and "thinking" not in kw and "betas" not in kw
    assert r.text == "hello" and r.notes == []


def test_supports_progress_updates():
    for m in ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5"]:
        assert supports_progress_updates(m), m
    for m in ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "claude-opus-4-8", "claude-opus-5-5-x"]:
        assert not supports_progress_updates(m), m
