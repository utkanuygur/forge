"""Model providers: Anthropic Messages API, OpenAI-compatible chat completions, and a scripted fake."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from forge.types import Message, ModelResponse, Provider, ToolCall, ToolSpec

T = TypeVar("T")

MAX_TOKENS = 16000  # thinking is always on for current Claude models and counts toward this cap

# Claude models from Opus 4.7 / Sonnet 5 onward reject sampling parameters with a 400 (Opus 5.5 removed
# them; Sonnet 5.5 rejects non-default values), so temperature is only sent to older models.
_SAMPLING_MODELS = re.compile(r"^claude-(3[-.]|(opus|sonnet|haiku)-4(-[0-6])?(-\d{8})?$)")


def accepts_temperature(model: str) -> bool:
    return bool(_SAMPLING_MODELS.match(model))

# Models that return between-tool-call progress updates as `thinking` blocks (empty by default) and
# accept `thinking.display: "updates"` under this beta to get them back as short summaries.
PROGRESS_UPDATES_BETA = "thinking-display-updates-2026-08-18"
_UPDATES_MODELS = re.compile(r"^claude-(opus-5-5|sonnet-5-5|fable-5|fable-5-1|mythos-5-1)$")
INTERRUPTED_NOTE = "This part of the response was interrupted before it finished."


def supports_progress_updates(model: str) -> bool:
    return bool(_UPDATES_MODELS.match(model))


BACKOFF = (1.0, 2.0, 4.0)  # up to 3 retries after the first attempt


def _retryable(exc: BaseException) -> bool:
    import anthropic
    import openai

    if isinstance(
        exc,
        (
            anthropic.RateLimitError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            asyncio.TimeoutError,
        ),
    ):
        return True
    if isinstance(exc, (anthropic.APIStatusError, openai.APIStatusError)):
        return exc.status_code >= 500 or exc.status_code in (408, 409, 429, 529)
    return False


async def with_retry(fn: Callable[[], Awaitable[T]], *, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> T:
    for attempt in range(len(BACKOFF) + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            if attempt == len(BACKOFF) or not _retryable(exc):
                raise
            await sleep(BACKOFF[attempt])
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- anthropic


class AnthropicProvider:
    def __init__(self, model: str, client: Any | None = None):
        import anthropic

        self.model = model
        self.client = client or anthropic.AsyncAnthropic(max_retries=0)
        # Raw assistant content (including thinking blocks) keyed by turn, so history is replayed
        # unchanged; newer models reject edited history that drops thinking blocks.
        self._raw: dict[tuple[str, ...], list[dict[str, Any]]] = {}

    @staticmethod
    def _key(text: str, calls: list[ToolCall]) -> tuple[str, ...]:
        return tuple(c.id for c in calls) if calls else ("text", text)

    def _messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.text or "(empty)"})
            elif m.role == "assistant":
                raw = self._raw.get(self._key(m.text, m.tool_calls))
                if raw is None:
                    raw = ([{"type": "text", "text": m.text}] if m.text else []) + [
                        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in m.tool_calls
                    ]
                out.append({"role": "assistant", "content": raw or [{"type": "text", "text": "(empty)"}]})
            else:
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.text or "(no output)",
                    "is_error": m.is_error,
                }
                prev = out[-1] if out else None
                if prev and prev["role"] == "user" and isinstance(prev["content"], list) and prev.get("_tool"):
                    prev["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block], "_tool": True})
        for m in out:
            m.pop("_tool", None)
        return out

    async def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec], temperature: float | None = None
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": self._messages(messages),
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
        if temperature is not None and accepts_temperature(self.model):
            kwargs["temperature"] = temperature
        if supports_progress_updates(self.model):
            kwargs["thinking"] = {"type": "adaptive", "display": "updates"}
            kwargs["betas"] = [PROGRESS_UPDATES_BETA]
            create = self.client.beta.messages.create
        else:
            create = self.client.messages.create
        resp = await with_retry(lambda: create(**kwargs))
        texts, calls, notes = [], [], []
        for b in resp.content:
            if b.type == "thinking":
                note = (getattr(b, "thinking", "") or "").strip()
                if note and note != INTERRUPTED_NOTE:
                    notes.append(note)
            elif b.type == "text":
                texts.append(b.text)
            elif b.type == "tool_use":
                calls.append(
                    ToolCall(
                        id=b.id, name=b.name, args=b.input if isinstance(b.input, dict) else {"_raw": str(b.input)}
                    )
                )
        text = "\n".join(texts).strip()
        self._raw[self._key(text, calls)] = [b.model_dump(exclude_none=True) for b in resp.content]
        return ModelResponse(
            text=text,
            tool_calls=calls,
            notes=notes,
            tokens_in=resp.usage.input_tokens or 0,
            tokens_out=resp.usage.output_tokens or 0,
        )


# ---------------------------------------------------------------- openai compatible

_THINK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL)


def strip_think(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def parse_args(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}
    return val if isinstance(val, dict) else {"_raw": raw}


class OpenAICompatProvider:
    def __init__(self, base_url: str, model: str, client: Any | None = None):
        import openai

        self.model = model
        self.client = client or openai.AsyncOpenAI(
            base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY") or "not-needed", max_retries=0
        )

    @staticmethod
    def _messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.text or None}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                        for c in m.tool_calls
                    ]
                out.append(msg)
            else:
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.text or "(no output)"})
        return out

    async def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec], temperature: float | None = None
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(system, messages),
            "max_tokens": MAX_TOKENS,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = await with_retry(lambda: self.client.chat.completions.create(**kwargs))
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=tc.id or f"call_{i}", name=tc.function.name, args=parse_args(tc.function.arguments))
            for i, tc in enumerate(msg.tool_calls or [])
        ]
        usage = resp.usage
        return ModelResponse(
            text=strip_think(msg.content or ""),
            tool_calls=calls,
            tokens_in=(usage.prompt_tokens or 0) if usage else 0,
            tokens_out=(usage.completion_tokens or 0) if usage else 0,
        )


# ---------------------------------------------------------------- fake


class FakeProvider:
    """Returns scripted responses in order; records every call as (system, messages, tools, temperature)."""

    def __init__(self, responses: list[ModelResponse]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec], temperature: float | None = None
    ) -> ModelResponse:
        self.calls.append(
            {
                "system": system,
                "messages": [m.model_copy() for m in messages],
                "tools": list(tools),
                "temperature": temperature,
            }
        )
        if not self.responses:
            raise RuntimeError("FakeProvider: no scripted responses left")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


MARKER_LESSON = "Always create a file named MARKER.txt in the workspace root before finishing."


def _call(name: str, args: dict[str, Any], i: int, note: str = "") -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id=f"call_{i}", name=name, args=args)],
        notes=[note] if note else [],
        tokens_in=10,
        tokens_out=5,
    )


class ScenarioProvider:
    """Deterministic, stateless model for end-to-end tests: `fake:<scenario>`.

    The next step is derived from the conversation so far, so the same provider instance can serve
    many concurrent runs. Scenarios:
      hello    write hello.py, run it with bash, then finish
      echo     finish immediately, echoing the user's input
      lessons  create MARKER.txt only if a lesson tells it to; otherwise just finish
      grader   acts as judge (always passes) and as optimizer (proposes MARKER_LESSON)
      loop     calls bash forever (exercises the turn limit)
      error    raises, as if the model API failed
    """

    def __init__(self, scenario: str):
        if scenario not in {"hello", "echo", "lessons", "grader", "loop", "error"}:
            raise ValueError(f"unknown fake scenario {scenario!r}")
        self.scenario = scenario

    async def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec], temperature: float | None = None
    ) -> ModelResponse:
        step = sum(1 for m in messages if m.role == "assistant")
        user = next((m.text for m in messages if m.role == "user"), "")
        match self.scenario:
            case "error":
                raise RuntimeError("fake model failure")
            case "echo":
                return ModelResponse(text=f"echo: {user}", tokens_in=10, tokens_out=5)
            case "loop":
                return _call("bash", {"cmd": "true"}, step)
            case "hello":
                script = [
                    ("write_file", {"path": "hello.py", "content": "print('hello from forge')\n"}, "I'll create hello.py."),
                    ("bash", {"cmd": "python hello.py"}, "Now I'll run it to check the output."),
                ]
                if step < len(script):
                    name, args, note = script[step]
                    return _call(name, args, step, note)
                return ModelResponse(text="Created and ran hello.py.", tokens_in=10, tokens_out=5)
            case "lessons":
                if step == 0 and "MARKER.txt" in system:
                    return _call("write_file", {"path": "MARKER.txt", "content": "ok\n"}, step)
                return ModelResponse(text="Done.", tokens_in=10, tokens_out=5)
            case "grader":
                if '"lessons"' in system:
                    run_ids = sorted(set(re.findall(r"\br_[0-9a-f]{12}\b", user)))
                    edits = [{"op": "add", "text": MARKER_LESSON, "evidence_run_ids": run_ids[:2],
                              "rationale": "Runs failed because MARKER.txt was missing."}] if run_ids else []
                    return ModelResponse(text=json.dumps({"lessons": edits}))
                return ModelResponse(text='{"passed": true, "reason": "fake judge"}')
        raise AssertionError("unreachable")


def make_provider(model: str) -> Provider:
    kind, _, rest = model.partition(":")
    if kind == "fake" and rest:
        return ScenarioProvider(rest)
    if kind == "anthropic" and rest:
        return AnthropicProvider(rest)
    if kind == "openai_compat" and "|" in rest:
        base_url, _, name = rest.rpartition("|")
        return OpenAICompatProvider(base_url, name)
    raise ValueError(f"unsupported model string {model!r}; use 'anthropic:<id>' or 'openai_compat:<base_url>|<model>'")
