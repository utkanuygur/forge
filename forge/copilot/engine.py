"""One assistant turn: the model and tool loop for a user message, with confirmation gating and limits.

Every step is persisted as a copilot message (forge.db) and published to the turn's subscribers (the SSE stream of
POST /copilot/sessions/{id}/messages, and GET .../events for reconnects). The turn runs as its own task, so a
dropped connection does not stop it; Stop (cancel) does.

Gating: read tools run at once. All write calls of one model turn are validated and summarized, then announced
with ONE `confirm_request`; the loop waits for POST .../confirm. Approve runs them in order; deny (or no answer
within CONFIRM_TIMEOUT) returns an error result to the model for each and nothing changes. `execute` itself
refuses a write tool without an approval, so no other path can run one.

Limits per user message: MAX_STEPS tool calls, MAX_SECONDS of wall clock (time spent waiting on the user's
confirmation and running the actions they approved is not counted), MAX_MODEL_TURNS model calls, tool outputs capped at tools.MAX_TOOL_OUTPUT chars.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

from forge import db, pricing
from forge.copilot import prompt, tools
from forge.copilot.api_client import ForgeApi, ToolError
from forge.types import Message, ToolCall

log = logging.getLogger("forge.copilot")

MAX_STEPS = 15
MAX_SECONDS = 120.0
MAX_MODEL_TURNS = 20
CONFIRM_TIMEOUT = 600.0
MAX_USER_CHARS = 8000
MAX_TEXT_CHARS = 20000
HISTORY_MESSAGES = 80
OLD_OUTPUT_CHARS = 1500
METERED_KIND = "assist:copilot"  # grader_calls.kind; shows as kind "copilot" in an agent's assist cost
NO_AGENT_BUCKET = "copilot"      # grader_calls.agent_id when the page names no agent


class Busy(Exception):
    pass


def model_name() -> str:
    from forge.server.evals import grader_model

    return grader_model()


def make_model(agent_id: str | None) -> Any:
    from forge.providers import make_provider
    from forge.server.evals import MeteredProvider

    return MeteredProvider(make_provider(model_name()), model_name(), agent_id or NO_AGENT_BUCKET, METERED_KIND)


class Turn:
    """The live state of one user message: its events, pending confirmations and task."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.events: list[dict] = []
        self.cond = asyncio.Condition()
        self.done = False
        self.task: asyncio.Task | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.resolved: set[str] = set()

    async def emit(self, type: str, payload: dict) -> dict:
        row = db.add_copilot_message(self.session_id, type, payload)
        ev = {"seq": row["seq"], "type": type, "payload": payload, "created_at": row["created_at"]}
        self.events.append(ev)
        async with self.cond:
            self.cond.notify_all()
        return ev

    async def finish(self) -> None:
        self.done = True
        for fut in self.pending.values():
            if not fut.done():
                fut.set_result((False, "stopped"))
        async with self.cond:
            self.cond.notify_all()

    async def stream(self, after: int = -1) -> AsyncIterator[dict]:
        i = 0
        while True:
            async with self.cond:
                await self.cond.wait_for(lambda: i < len(self.events) or self.done)
            while i < len(self.events):
                ev = self.events[i]
                i += 1
                if ev["seq"] > after:
                    yield ev
            if self.done and i >= len(self.events):
                return


_turns: dict[str, Turn] = {}  # session id -> its latest turn (running or finished)


def active(session_id: str) -> Turn | None:
    t = _turns.get(session_id)
    return t if t is not None and not t.done else None


def latest(session_id: str) -> Turn | None:
    return _turns.get(session_id)


def pending_confirm(session_id: str) -> dict | None:
    """The confirm_request still waiting for an answer, if any."""
    t = active(session_id)
    if t is None:
        return None
    for ev in reversed(t.events):
        if ev["type"] == "confirm_request" and ev["payload"]["id"] in t.pending:
            return ev
    return None


def resolve(session_id: str, confirm_id: str, approve: bool) -> None:
    """Answers a confirmation. KeyError: unknown id; ValueError: already answered or the turn ended."""
    t = _turns.get(session_id)
    fut = t.pending.get(confirm_id) if t else None
    if fut is None or fut.done():
        known = any(m["type"] == "confirm_request" and m["payload"].get("id") == confirm_id
                    for m in db.list_copilot_messages(session_id))
        if known:
            raise ValueError("this confirmation was already answered or has expired")
        raise KeyError(confirm_id)
    fut.set_result((bool(approve), "user"))


def cancel(session_id: str) -> bool:
    t = active(session_id)
    if t is None or t.task is None:
        return False
    t.task.cancel()
    return True


def start(session: dict, text: str, context: dict, api: ForgeApi, *, model: Any = None) -> Turn:
    if active(session["id"]) is not None:
        raise Busy("the assistant is still answering the previous message")
    turn = Turn(session["id"])
    _turns[session["id"]] = turn
    turn.task = asyncio.create_task(_main(turn, session, text, context, api, model))
    return turn


# ---------------------------------------------------------------- history


def load_history(session_id: str) -> list[Message]:
    """The model conversation rebuilt from stored messages. Calls without a stored result (a stopped turn) get
    a synthetic error result so the history stays valid; older tool outputs are clipped."""
    msgs: list[Message] = []
    open_calls: dict[str, str] = {}

    def close() -> None:
        for cid in list(open_calls):
            msgs.append(Message(role="tool", tool_call_id=cid, text="(not run: the request was stopped)",
                                is_error=True))
        open_calls.clear()

    for row in db.list_copilot_messages(session_id):
        p = row["payload"] or {}
        if row["type"] == "user":
            close()
            msgs.append(Message(role="user", text=p.get("model_text") or p.get("text") or ""))
        elif row["type"] == "assistant" and "tool_calls" in p:
            close()
            calls = [ToolCall(**c) for c in p.get("tool_calls") or []]
            msgs.append(Message(role="assistant", text=p.get("text") or "", tool_calls=calls))
            open_calls.update({c.id: c.name for c in calls})
        elif row["type"] == "tool_result" and p.get("id") in open_calls:
            open_calls.pop(p["id"])
            msgs.append(Message(role="tool", tool_call_id=p["id"], text=p.get("output") or "",
                                is_error=not p.get("ok", True)))
    close()
    if len(msgs) > HISTORY_MESSAGES:  # keep whole exchanges: cut at a user message
        cut = next((i for i in range(len(msgs) - HISTORY_MESSAGES, len(msgs)) if msgs[i].role == "user"), 0)
        msgs = msgs[cut:]
    users = [i for i, m in enumerate(msgs) if m.role == "user"]
    recent = users[-2] if len(users) >= 2 else 0
    for i, m in enumerate(msgs[:recent]):
        if m.role == "tool" and len(m.text) > OLD_OUTPUT_CHARS:
            msgs[i] = m.model_copy(update={"text": m.text[:OLD_OUTPUT_CHARS] + "\n[... clipped ...]"})
    return msgs


# ---------------------------------------------------------------- the loop


def batch_summary(items: list[dict]) -> str:
    if len(items) == 1:
        return items[0]["summary"]
    if all(i["tool"] == "create_eval_case" for i in items) and len({i["args"].get("slug") for i in items}) == 1:
        names = ", ".join(str(i["args"].get("name")) for i in items)
        return f"Add {len(items)} eval cases to {items[0]['args'].get('slug')}: {names}. Free until evaluated."
    return f"{len(items)} changes, listed below."


async def execute(t: tools.CopilotTool, args: dict, ctx: tools.ToolContext, secrets: list[str], *,
                  approved: bool) -> tuple[tools.ToolResult, bool]:
    """Runs one tool. Write tools need `approved`; this is the only place a tool runs."""
    if t.risk == "write" and not approved:
        raise PermissionError(f"write tool {t.name} needs the user's approval")
    try:
        res, ok = await t.run(ctx, args), True
    except ToolError as exc:
        res, ok = tools.ToolResult(f"Error: {exc}", str(exc)), False
    except Exception as exc:  # noqa: BLE001  a tool bug must not end the conversation
        log.exception("copilot tool %s failed", t.name)
        res, ok = tools.ToolResult(f"Error: {type(exc).__name__}: {exc}", f"{type(exc).__name__}"), False
    res.text = tools.cap(tools.redact(res.text, secrets))
    res.summary = tools.clip(tools.redact(res.summary, secrets), 160)
    return res, ok


def result_payload(call: ToolCall, t: tools.CopilotTool | None, res: tools.ToolResult, ok: bool) -> dict:
    return {"id": call.id, "tool": call.name, "risk": t.risk if t else "read", "ok": ok, "summary": res.summary,
            "output": res.text, "links": res.links, "refresh": res.refresh if ok else []}


def call_payload(call: ToolCall, t: tools.CopilotTool | None, secrets: list[str]) -> dict:
    return {"id": call.id, "tool": call.name, "args": tools.redact_obj(call.args, secrets),
            "risk": t.risk if t else "read"}


async def _calls(turn: Turn, calls: list[ToolCall], ctx: tools.ToolContext, secrets: list[str],
                 steps: int) -> tuple[list[Message], float, int]:
    """Runs one model turn's tool calls. Returns (tool messages in call order, seconds waited, steps)."""
    out: dict[str, Message] = {}
    writes: list[tuple[ToolCall, tools.CopilotTool, str]] = []

    async def fail(call: ToolCall, t: tools.CopilotTool | None, message: str) -> None:
        res = tools.ToolResult(f"Error: {message}", message)
        await turn.emit("tool_call", call_payload(call, t, secrets))
        await turn.emit("tool_result", result_payload(call, t, res, False))
        out[call.id] = Message(role="tool", tool_call_id=call.id, text=res.text, is_error=True)

    for call in calls:
        t = tools.TOOLS.get(call.name)
        if steps >= MAX_STEPS:
            await fail(call, t, f"Step limit of {MAX_STEPS} tool calls reached for this message. Answer the user "
                                "now without calling more tools.")
            continue
        steps += 1
        if t is None:
            await fail(call, None, f"unknown tool {call.name!r}")
        elif err := tools.validate_args(t, call.args):
            await fail(call, t, err)
        elif t.risk == "read":
            await turn.emit("tool_call", call_payload(call, t, secrets))
            res, ok = await execute(t, call.args, ctx, secrets, approved=False)
            await turn.emit("tool_result", result_payload(call, t, res, ok))
            out[call.id] = Message(role="tool", tool_call_id=call.id, text=res.text, is_error=not ok)
        else:
            assert t.describe is not None
            try:
                summary = tools.clip(tools.redact(await t.describe(ctx, call.args), secrets), 400)
            except ToolError as exc:
                await fail(call, t, str(exc))
                continue
            writes.append((call, t, summary))

    waited = 0.0
    if writes:
        items = [{"call_id": c.id, "tool": c.name, "args": tools.redact_obj(c.args, secrets), "summary": s}
                 for c, _, s in writes]
        confirm_id = db.new_id("cf")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        turn.pending[confirm_id] = fut
        await turn.emit("confirm_request", {
            "id": confirm_id, "tool": items[0]["tool"] if len({i["tool"] for i in items}) == 1 else "multiple",
            "args": items[0]["args"] if len(items) == 1 else [i["args"] for i in items],
            "summary": batch_summary(items), "items": items})
        t0 = time.monotonic()
        try:
            approved, by = await asyncio.wait_for(asyncio.shield(fut), CONFIRM_TIMEOUT)
        except asyncio.TimeoutError:
            approved, by = False, "timeout"
        finally:
            turn.pending.pop(confirm_id, None)
        await turn.emit("confirm_resolved", {"id": confirm_id, "approved": approved, "by": by})
        for call, t, _ in writes:
            await turn.emit("tool_call", call_payload(call, t, secrets))
            if approved:
                res, ok = await execute(t, call.args, ctx, secrets, approved=True)
            else:
                why = "did not answer in time" if by == "timeout" else "denied this action"
                res, ok = tools.ToolResult(f"Error: the user {why}, so nothing was changed. Do not retry it; ask "
                                           "what they want instead.", "denied" if by != "timeout" else "expired"), False
            await turn.emit("tool_result", result_payload(call, t, res, ok))
            out[call.id] = Message(role="tool", tool_call_id=call.id, text=res.text, is_error=not ok)
        # Waiting for the user and running what they approved (a grader call can take a minute) are both off
        # the clock, so an approved action always gets its follow-up answer.
        waited = time.monotonic() - t0
    return [out[c.id] for c in calls], waited, steps


async def _main(turn: Turn, session: dict, text: str, context: dict, api: ForgeApi, model: Any) -> None:
    usage = {"tokens_in": 0, "tokens_out": 0, "steps": 0}

    async def final(status: str, message: str = "") -> None:
        cost = pricing.cost(model_name(), usage["tokens_in"], usage["tokens_out"])
        await turn.emit("final", {"status": status, "message": message, **usage, "cost_usd": cost})

    try:
        await _loop(turn, session, text, context, api, model, usage, final)
    except asyncio.CancelledError:
        await final("stopped", "Stopped.")
    except Exception as exc:  # noqa: BLE001
        log.exception("copilot turn failed")
        await turn.emit("error", {"message": f"{type(exc).__name__}: {exc}"})
        await final("error", "Something went wrong.")
    finally:
        await turn.finish()


async def _loop(turn: Turn, session: dict, text: str, context: dict, api: ForgeApi, model: Any, usage: dict,
                final: Any) -> None:
    history = load_history(session["id"])
    model_text = prompt.user_text(text, context)
    await turn.emit("user", {"text": text, "context": context, "model_text": model_text})
    db.update_copilot_session(session["id"], context=context,
                              **({"title": tools.clip(text, 60)} if not session.get("title") else {}))
    agent = db.get_agent_by_slug(context["agent_slug"]) if context.get("agent_slug") else None
    provider = model or make_model(agent["id"] if agent else None)
    ctx = tools.ToolContext(api=api, context=context)
    secrets = tools.secret_values()
    specs = tools.specs()
    messages = [*history, Message(role="user", text=model_text)]
    deadline = time.monotonic() + MAX_SECONDS
    for _ in range(MAX_MODEL_TURNS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return await final("limit", "Stopped: this request reached the 2 minute limit.")
        try:
            resp = await asyncio.wait_for(provider.complete(prompt.SYSTEM, messages, specs, None), remaining)
        except asyncio.TimeoutError:
            return await final("limit", "Stopped: this request reached the 2 minute limit.")
        except Exception as exc:  # noqa: BLE001
            log.exception("copilot model call failed")
            await turn.emit("error", {"message": f"The assistant model failed: {type(exc).__name__}: {exc}"})
            return await final("error", "The assistant model failed.")
        usage["tokens_in"] += resp.tokens_in
        usage["tokens_out"] += resp.tokens_out
        for note in resp.notes:
            await turn.emit("assistant", {"text": tools.redact(note, secrets), "kind": "progress"})
        if resp.stop_reason == "refusal":
            await turn.emit("error", {"message": f"The model declined: {resp.stop_detail or 'no reason given'}"})
            return await final("error", "The model declined this request.")
        reply = tools.redact(resp.text or "", secrets)[:MAX_TEXT_CHARS]
        calls = resp.tool_calls
        messages.append(Message(role="assistant", text=reply, tool_calls=calls))
        await turn.emit("assistant", {"text": reply, "kind": "progress" if calls else "final",
                                      "tool_calls": [c.model_dump() for c in calls]})
        if not calls:
            return await final("done")
        if usage["steps"] >= MAX_STEPS:
            await _calls(turn, calls, ctx, secrets, usage["steps"])  # records each call as refused
            return await final("limit", f"Stopped after {MAX_STEPS} tool steps. Ask me to continue if needed.")
        results, waited, usage["steps"] = await _calls(turn, calls, ctx, secrets, usage["steps"])
        deadline += waited  # time spent waiting for the user does not count
        messages.extend(results)
    await final("limit", f"Stopped after {MAX_MODEL_TURNS} model turns.")
