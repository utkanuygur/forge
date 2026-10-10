"""Model providers: Anthropic Messages API, OpenAI-compatible chat completions, and a scripted fake."""

from __future__ import annotations

import asyncio
import hashlib
import json
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


class ModelNotFoundError(RuntimeError):
    """The provider answered 404 for the model: it does not exist there (or the base URL is wrong).

    Evals stop the remaining runs of a version when a run fails with this (forge.server.evals)."""


def _not_found(exc: BaseException) -> bool:
    import anthropic
    import openai

    return isinstance(exc, (anthropic.NotFoundError, openai.NotFoundError))


def _api_detail(exc: BaseException) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return str(exc)


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

CACHE = {"type": "ephemeral"}  # 5-minute TTL: agent turns run well under 5 minutes apart


def with_cache_breakpoint(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Marks the last content block of the conversation with cache_control (breakpoint 2 of 4).

    Each request then reads the whole prior conversation from cache and writes only the newest turn.
    The marked block is copied so cached raw assistant content is never mutated.
    """
    if not messages:
        return messages
    last = dict(messages[-1])
    content = last["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [dict(b) for b in content]
    if blocks:
        blocks[-1]["cache_control"] = CACHE
    last["content"] = blocks
    return [*messages[:-1], last]


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
                # Always a block list, so the turn renders the same whether or not it carries the breakpoint.
                out.append({"role": "user", "content": [{"type": "text", "text": m.text or "(empty)"}]})
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
        # Prompt caching, at most 2 of the 4 allowed breakpoints. Breakpoint 1 on the system block caches
        # tools + system (render order is tools, system, messages; both are byte-stable within a run).
        # Breakpoint 2 on the last message block caches the growing conversation, only for tool-using
        # loops: one-shot judge and optimizer calls have no next turn to read it, so it would be pure cost.
        api_messages = self._messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": system, "cache_control": CACHE}] if system else system,
            "messages": with_cache_breakpoint(api_messages) if tools else api_messages,
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
        try:
            resp = await with_retry(lambda: create(**kwargs))
        except Exception as exc:
            if _not_found(exc):
                raise ModelNotFoundError(f"Model '{self.model}' is not available to this Claude API key "
                                         f"({_api_detail(exc)})") from exc
            raise
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
        usage = resp.usage
        stop_reason = getattr(resp, "stop_reason", None)
        detail = None
        if stop_reason == "refusal" and (sd := getattr(resp, "stop_details", None)) is not None:
            detail = getattr(sd, "category", None) or getattr(sd, "explanation", None)
        return ModelResponse(
            text=text,
            tool_calls=calls,
            notes=notes,
            tokens_in=usage.input_tokens or 0,
            tokens_out=usage.output_tokens or 0,
            stop_reason=stop_reason,
            stop_detail=detail,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
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


# finish_reason -> normalized stop_reason (unknown values pass through unchanged)
OPENAI_STOP = {"stop": "end_turn", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens",
               "content_filter": "refusal"}


class OpenAICompatProvider:
    def __init__(self, base_url: str, model: str, client: Any | None = None):
        import openai

        from forge import settings

        self.model = model
        self.base_url = base_url.rstrip("/")
        # The endpoint's own key variable (Settings > OpenAI compatible endpoints), else OPENAI_API_KEY.
        self.client = client or openai.AsyncOpenAI(
            base_url=base_url, api_key=settings.openai_key_for(base_url) or "not-needed", max_retries=0
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
        try:
            resp = await with_retry(lambda: self.client.chat.completions.create(**kwargs))
        except Exception as exc:
            if _not_found(exc):
                raise ModelNotFoundError(f"Model '{self.model}' is not available on {self.base_url} "
                                         f"({_api_detail(exc)})") from exc
            raise
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=tc.id or f"call_{i}", name=tc.function.name, args=parse_args(tc.function.arguments))
            for i, tc in enumerate(msg.tool_calls or [])
        ]
        usage = resp.usage
        finish = getattr(resp.choices[0], "finish_reason", None)
        return ModelResponse(
            text=strip_think(msg.content or ""),
            tool_calls=calls,
            tokens_in=(usage.prompt_tokens or 0) if usage else 0,
            tokens_out=(usage.completion_tokens or 0) if usage else 0,
            stop_reason=OPENAI_STOP.get(finish, finish) if finish else None,
            stop_detail="content_filter" if finish == "content_filter" else None,
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


_FLAKY_COUNTS: dict[str, int] = {}  # fake:flaky runs started per sha256(system, input)

MARKER_LESSON = "Always create a file named MARKER.txt in the workspace root before finishing."


# What `fake:grader` returns when called with the drafting prompt (forge.drafting.DRAFT_SYSTEM): a fixed, valid
# draft so drafting can be tested end to end without API calls. The fake ignores the description.
FAKE_DRAFT: dict[str, Any] = {
    "name": "Issue triager",
    "slug": "issue-triager",
    "description": "Labels new GitHub issues and writes a short triage summary.",
    "system_prompt": (
        "You are an issue triager for a software project. A task gives you one or more GitHub issues; it is done "
        "when every issue has a label, a priority and a one line reason in triage.md.\n\n"
        "How to work:\n"
        "1. Read each issue fully, including any stack trace or reproduction steps.\n"
        "2. Choose exactly one label from bug, feature, question or docs, and a priority from P0 to P3.\n"
        "3. Write triage.md as a Markdown table with the columns issue, label, priority and reason.\n"
        "4. Re-read the table and check that every issue from the task appears once.\n\n"
        "Final reply: the number of issues triaged and any that need a human decision.\n\n"
        "Avoid: inventing issue numbers, guessing a priority without evidence, and printing the GITHUB_TOKEN."
    ),
    "tools": ["bash", "read_file", "write_file"],
    "network": True,
    "network_reason": "Needs the GitHub REST API to read issues.",
    "suggested_secrets": [{"name": "GITHUB_TOKEN", "why": "Authenticates GitHub API requests for private repositories."}],
    "seed_files_hint": "None needed; issues come from the task input or the GitHub API.",
    "eval_cases": [
        {"name": "crash-report", "input": "Triage issue #12: the app crashes on start with a null pointer trace.",
         "checker_type": "judge", "split": "train",
         "checker": "PASS only if triage.md exists, labels issue #12 as bug with priority P0 or P1, and gives a reason "
                    "that cites the crash. FAIL if the issue number is changed."},
        {"name": "feature-request", "input": "Triage issue #31: please add a dark mode to the settings page.",
         "checker_type": "judge", "split": "train",
         "checker": "PASS only if triage.md labels issue #31 as feature with priority P2 or P3 and the reason "
                    "mentions dark mode. FAIL if it is labeled bug."},
        {"name": "triage-file", "input": "Triage issue #7: the README install command is out of date.",
         "checker_type": "shell", "split": "train", "checker": "test -f triage.md && grep -q '#7' triage.md"},
        {"name": "mixed-batch", "input": "Triage issues #40 (typo in docs) and #41 (how do I configure logging?).",
         "checker_type": "judge", "split": "holdout",
         "checker": "PASS only if triage.md has exactly one row each for #40 labeled docs and #41 labeled question, "
                    "each with a priority and reason. FAIL if either issue is missing."},
    ],
}


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
      grader   acts as judge (always passes), as optimizer (proposes MARKER_LESSON), given the drafting
               prompt as agent drafter (returns FAKE_DRAFT) and, given an assist kind's system prompt,
               as that kind's suggester (returns the kind's registered `fake` reply, see forge.assist)
      loop     calls bash forever (exercises the turn limit)
      error    raises, as if the model API failed
      missing  raises ModelNotFoundError, as if the model does not exist on its provider (evals stop early)
      flaky    alternates per run: the 1st, 3rd, 5th, ... run with a given (system prompt, input) creates
               FLAKY.txt, the 2nd, 4th, ... just finish. The only stateful scenario: a process-wide counter
               keyed by a hash of (system, input) advances when a run makes its first call, so k concurrent
               runs of one case always yield ceil(k/2) passes of `test -f FLAKY.txt` in any interleaving
               (k=3 -> 2/3 passes the majority, k=2 -> 1/2 fails it). Restarting the server resets it.
      risky    runs `rm -rf build` with bash, then finishes saying whether it was allowed (approval policies)
      tools    calls non-builtin tools (HTTP, MCP, agent__) as told by a JSON hint in the user input, then
               finishes with their outputs; see tools_hint()
      session  turn 1 writes hello.py; later turns `cat hello.py` and report what they saw (playground)
      slow     runs `sleep 30` forever (exercises cancel / Stop)
      copilot  the Forge assistant's model (forge.copilot.fake): adds N eval cases, explains a failed run,
               refuses secrets; `grader` also answers like this when given the assistant's system prompt
    """

    def __init__(self, scenario: str):
        if scenario not in {"hello", "echo", "lessons", "grader", "loop", "error", "flaky", "risky", "tools", "session", "slow",
                            "missing", "copilot"}:
            raise ValueError(f"unknown fake scenario {scenario!r}")
        self.scenario = scenario

    async def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec], temperature: float | None = None
    ) -> ModelResponse:
        # Steps count from the latest user message, so playground sessions (history + new input) replay
        # the scenario per turn; ids stay unique across turns.
        last_user = max(i for i, m in enumerate(messages) if m.role == "user")
        turn = sum(1 for m in messages if m.role == "user") - 1
        step = sum(1 for m in messages[last_user:] if m.role == "assistant")
        user = messages[last_user].text
        if turn:
            def _call_t(name: str, args: dict[str, Any], i: int, note: str = "") -> ModelResponse:
                r = _call(name, args, i, note)
                r.tool_calls[0].id = f"call_t{turn}_{i}"
                return r
        else:
            _call_t = _call
        from forge.copilot.prompt import MARK as COPILOT_MARK

        if self.scenario == "copilot" or (self.scenario == "grader" and system.startswith(COPILOT_MARK)):
            from forge.copilot.fake import respond

            return respond(messages)
        match self.scenario:
            case "error":
                raise RuntimeError("fake model failure")
            case "missing":
                raise ModelNotFoundError("Model 'missing' is not available on fake (model not found)")
            case "echo":
                return ModelResponse(text=f"echo: {user}", tokens_in=10, tokens_out=5)
            case "loop":
                return _call_t("bash", {"cmd": "true"}, step)
            case "slow":
                return _call_t("bash", {"cmd": "sleep 30"}, step, "Waiting a while.")
            case "session":
                # Turn 1 writes hello.py; later turns read it back from the carried-over workspace and
                # report how many earlier turns the history contained.
                if turn == 0:
                    if step == 0:
                        return _call_t("write_file", {"path": "hello.py", "content": "print('hello from forge')\n"}, 0,
                                       "I'll create hello.py.")
                    return ModelResponse(text="Created hello.py.", tokens_in=10, tokens_out=5)
                if step == 0:
                    return _call_t("bash", {"cmd": "cat hello.py"}, 0, "Reading the file from the last turn.")
                seen = messages[-1].text if messages[-1].role == "tool" else ""
                content = seen.split("--- stdout ---\n", 1)[-1].split("\n--- stderr ---", 1)[0].strip()
                return ModelResponse(text=f"Turn {turn + 1}: hello.py contains `{content}` "
                                          f"(history had {turn} earlier turn(s)).", tokens_in=10, tokens_out=5)
            case "hello":
                script = [
                    ("write_file", {"path": "hello.py", "content": "print('hello from forge')\n"}, "I'll create hello.py."),
                    ("bash", {"cmd": "python hello.py"}, "Now I'll run it to check the output."),
                ]
                if step < len(script):
                    name, args, note = script[step]
                    return _call_t(name, args, step, note)
                return ModelResponse(text="Created and ran hello.py.", tokens_in=10, tokens_out=5)
            case "flaky":
                if step == 0:
                    key = hashlib.sha256(f"{system}\0{user}".encode()).hexdigest()
                    n = _FLAKY_COUNTS.get(key, 0)
                    _FLAKY_COUNTS[key] = n + 1
                    if n % 2 == 0:
                        return _call("write_file", {"path": "FLAKY.txt", "content": f"run {n}\n"}, step)
                return ModelResponse(text="Done.", tokens_in=10, tokens_out=5)
            case "risky":
                if step == 0:
                    return _call("bash", {"cmd": "rm -rf build"}, step)
                last = next((m for m in reversed(messages) if m.role == "tool"), None)
                if last is not None and last.is_error:
                    return ModelResponse(text=f"Cleanup was not allowed, so build/ is unchanged. ({last.text})",
                                         tokens_in=10, tokens_out=5)
                return ModelResponse(text="Removed the build directory.", tokens_in=10, tokens_out=5)
            case "lessons":
                if step == 0 and "MARKER.txt" in system:
                    return _call("write_file", {"path": "MARKER.txt", "content": "ok\n"}, step)
                return ModelResponse(text="Done.", tokens_in=10, tokens_out=5)
            case "tools":
                calls = tools_hint(user)
                offered = [t.name for t in tools if t.name not in BUILTIN_TOOLS]
                if step < len(calls):
                    want, args = calls[step]
                    name = next((n for n in offered if n == want), None) or next(
                        (n for n in offered if want and n.startswith(want)), None) or (offered[0] if offered else "")
                    if not name:
                        return ModelResponse(text="No extra tools were offered.", tokens_in=10, tokens_out=5)
                    return _call(name, args, step)
                outputs = [m.text for m in messages if m.role == "tool"]
                return ModelResponse(text="Tool results:\n" + "\n---\n".join(outputs), tokens_in=10, tokens_out=5)
            case "grader":
                from forge.drafting import DRAFT_SYSTEM
                from forge.assist.registry import by_system_prompt

                if system == DRAFT_SYSTEM:
                    return ModelResponse(text=json.dumps(FAKE_DRAFT), tokens_in=10, tokens_out=5)
                if (assist := by_system_prompt(system)) is not None and assist.fake is not None:
                    return ModelResponse(text=json.dumps(assist.fake(user)), tokens_in=120, tokens_out=30)
                if '"lessons"' in system:
                    run_ids = sorted(set(re.findall(r"\br_[0-9a-f]{12}\b", user)))
                    edits = [{"op": "add", "text": MARKER_LESSON, "evidence_run_ids": run_ids[:2],
                              "rationale": "Runs failed because MARKER.txt was missing."}] if run_ids else []
                    return ModelResponse(text=json.dumps({"lessons": edits}), tokens_in=200, tokens_out=40)
                return ModelResponse(text='{"passed": true, "reason": "fake judge"}', tokens_in=50, tokens_out=10)
        raise AssertionError("unreachable")


BUILTIN_TOOLS = {"bash", "read_file", "write_file", "edit_file"}


def tools_hint(user: str) -> list[tuple[str, dict[str, Any]]]:
    """Calls requested by the `fake:tools` hint: the first JSON object in the input.

    `{"tool": "<name or prefix>", "args": {...}}` makes one call; `{"calls": [{"tool": .., "args": ..}, ..]}`
    makes several in order. A missing or unmatched `tool` picks the first non-builtin tool offered. Without a
    hint, one call with empty arguments is made.
    """
    start = user.find("{")
    hint: Any = {}
    if start >= 0:
        try:
            hint, _ = json.JSONDecoder().raw_decode(user[start:])
        except json.JSONDecodeError:
            hint = {}
    if not isinstance(hint, dict):
        hint = {}
    items = hint.get("calls") if isinstance(hint.get("calls"), list) else [hint]
    out = []
    for item in items:
        if isinstance(item, dict):
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            out.append((str(item.get("tool") or ""), args))
    return out or [("", {})]


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
