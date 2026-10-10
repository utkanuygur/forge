"""Playground sessions: rebuild a conversation from earlier turns and carry the workspace forward.

Each turn is an ordinary run. The next turn's model history is reconstructed from the stored events
of the previous turns (user input, assistant text, tool calls and results), truncated to a budget, and
its workspace starts from the previous turn's exported archive. Containers stay stateless.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import PurePosixPath
from typing import Any, Iterable

from forge.types import Message, ToolCall

HISTORY_BUDGET = 60_000  # characters across all reconstructed messages
TOOL_OUTPUT_CAP = 2_000  # characters kept per tool result in history
TIGHT_TOOL_OUTPUT_CAP = 300  # used when even the latest turn alone exceeds the budget
MAX_FILE_BYTES = 5_000_000


def clip(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head = cap * 2 // 5
    tail = cap - head
    return f"{text[:head]}\n[... {len(text) - cap} chars omitted from history ...]\n{text[-tail:]}"


def turn_messages(input: str, events: Iterable[dict[str, Any]], *, tool_cap: int = TOOL_OUTPUT_CAP,
                  status: str | None = None) -> list[Message]:
    """Messages for one finished turn. Unanswered tool calls (a cancelled or crashed turn) are dropped,
    and a turn without a final answer gets a short note so the next user message reads naturally."""
    msgs: list[Message] = [Message(role="user", text=input)]
    texts: list[str] = []
    calls: list[ToolCall] = []
    results: dict[str, Message] = {}
    final = ""

    def flush() -> None:
        nonlocal texts, calls, results
        answered = [c for c in calls if c.id in results]
        if answered:
            msgs.append(Message(role="assistant", text="\n\n".join(texts), tool_calls=answered))
            msgs.extend(results[c.id] for c in answered)
        elif texts:
            msgs.append(Message(role="assistant", text="\n\n".join(texts)))
        texts, calls, results = [], [], {}

    for e in sorted(events, key=lambda e: e["seq"]):
        p = e.get("payload") or {}
        kind = e["type"]
        if kind == "assistant":
            text = str(p.get("text") or "").strip()
            if not text:
                continue
            if results:  # a new model response begins after the previous one's tool results
                flush()
            if p.get("kind") == "final":
                final = text
            else:
                texts.append(text)
        elif kind == "tool_call":
            if results:
                flush()
            args = p.get("args") if isinstance(p.get("args"), dict) else {}
            calls.append(ToolCall(id=str(p.get("id")), name=str(p.get("name")), args=args))
        elif kind == "tool_result":
            cid = str(p.get("id"))
            if any(c.id == cid for c in calls):
                results[cid] = Message(role="tool", tool_call_id=cid, text=clip(str(p.get("output") or ""), tool_cap),
                                       is_error=bool(p.get("is_error")))
    if final and not calls:
        texts.append(final)
        final = ""
    flush()
    if final:
        msgs.append(Message(role="assistant", text=final))
    elif msgs[-1].role != "assistant":
        note = {"cancelled": "stopped by the user", "timeout": "hit its time or turn limit",
                "failed": "failed"}.get(status or "", "ended")
        msgs.append(Message(role="assistant", text=f"(This turn {note} before a final answer.)"))
    return msgs


def _size(msgs: list[Message]) -> int:
    return sum(len(m.text) + sum(len(str(c.args)) for c in m.tool_calls) for m in msgs)


def build_history(turns: list[tuple[str, list[dict[str, Any]], str | None]], *,
                  budget: int = HISTORY_BUDGET) -> list[Message]:
    """`turns` are (input, events, status), oldest first. Oldest turns are dropped whole to fit."""
    per_turn = [turn_messages(i, evs, status=s) for i, evs, s in turns]
    kept: list[list[Message]] = []
    total = 0
    for msgs in reversed(per_turn):
        size = _size(msgs)
        if kept and total + size > budget:
            break
        kept.insert(0, msgs)
        total += size
    if kept and total > budget:  # the latest turn alone is too big: squeeze its tool outputs
        i, evs, s = turns[len(turns) - len(kept)]
        kept[0] = turn_messages(i, evs, tool_cap=TIGHT_TOOL_OUTPUT_CAP, status=s)
    dropped = len(per_turn) - len(kept)
    out = [m for msgs in kept for m in msgs]
    if dropped and out:
        out[0] = out[0].model_copy(update={
            "text": f"[{dropped} earlier turn(s) of this session were omitted to fit the context.]\n\n{out[0].text}"})
    return out


# ---------------------------------------------------------------- workspace archives


def _safe(name: str) -> str | None:
    parts = [x for x in PurePosixPath(name.replace("\\", "/").lstrip("/")).parts if x not in ("", ".")]
    if not parts or ".." in parts or parts[0] == ".git":
        return None
    return "/".join(parts)


def read_archive(path: str) -> dict[str, bytes]:
    """All regular files of an exported workspace (except .git), as seed files for the next turn."""
    out: dict[str, bytes] = {}
    with tarfile.open(path, mode="r:*") as tar:
        for m in tar:
            if m.isfile() and m.size <= MAX_FILE_BYTES and (p := _safe(m.name)):
                f = tar.extractfile(m)
                out[p] = f.read() if f else b""
    return out


def tar_bytes(files: dict[str, bytes]) -> bytes:
    """Test helper: a .tar.gz laid out like an exported workspace."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"./{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()
