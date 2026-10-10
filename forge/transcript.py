"""Terminal transcript for `forge run`, in the style of Claude Code: narration, `● Tool(arg)` headers and
dim `⎿` result summaries. Plain ANSI only; colour is off for NO_COLOR and non-TTY output."""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
from typing import Any, TextIO

from forge.types import Event

PREVIEW_LINES = 3
LABEL_WIDTH = 80

_BASH = re.compile(r"^exit_code: (-?\d+)\n--- stdout ---\n(.*?)\n--- stderr ---\n(.*)$", re.DOTALL)
_TIMED_OUT = re.compile(r"\n?\[command timed out after (\d+)s and was killed\]\s*$")


def use_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def plain_markdown(text: str) -> str:
    """Strip the markdown that reads badly in a terminal: bold, italics markers, inline code, heading hashes."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        out.append(line)
    return "\n".join(out).strip()


def parse_bash(output: str) -> dict[str, Any]:
    """Split forge's bash tool output into exit code, stdout, stderr and the timed-out flag."""
    timed_out = bool(_TIMED_OUT.search(output))
    body = _TIMED_OUT.sub("", output)
    m = _BASH.match(body)
    if not m:
        return {"exit_code": None, "stdout": body, "stderr": "", "timed_out": timed_out}
    return {"exit_code": int(m.group(1)), "stdout": m.group(2), "stderr": m.group(3), "timed_out": timed_out}


def _lines(text: str) -> list[str]:
    text = text.rstrip("\n")
    return text.split("\n") if text else []


def _clip(s: str, n: int = LABEL_WIDTH) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def tool_label(name: str, args: dict[str, Any]) -> str:
    path = str(args.get("path", ""))
    if name == "bash":
        cmd = str(args.get("cmd") or args.get("command") or "")
        lines = cmd.strip().split("\n")
        first = lines[0] + (" …" if len(lines) > 1 else "")
        return f"Bash({_clip(first)})"
    if name == "read_file":
        rng = ""
        if args.get("start") or args.get("end"):
            rng = f":{args.get('start') or 1}-{args.get('end') or ''}".rstrip("-")
        return f"Read({path}{rng})"
    if name == "write_file":
        return f"Write({path})"
    if name == "edit_file":
        return f"Update({path})"
    if name.startswith("agent__"):
        return f"Agent({name.removeprefix('agent__')})"
    if name.startswith("mcp__") and name.count("__") >= 2:
        _, server, tool = name.split("__", 2)
        name = f"{server}.{tool}"
    return f"{name}({_clip(json.dumps(args, ensure_ascii=False), 60)})"


def line_diff_counts(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for d in difflib.ndiff(_lines(old), _lines(new)):
        if d.startswith("+ "):
            added += 1
        elif d.startswith("- "):
            removed += 1
    return added, removed


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def result_summary(name: str, args: dict[str, Any], output: str, is_error: bool,
                   payload: dict[str, Any] | None = None) -> tuple[list[str], bool]:
    """Lines to print under a tool header, and whether they are an error."""
    p = payload or {}
    if name.startswith("agent__") or p.get("child_run_id"):
        status = str(p.get("child_status") or ("failed" if is_error else "succeeded"))
        first = next((ln.strip() for ln in output.splitlines() if ln.strip()), "")
        line = f"{status}: {_clip(plain_markdown(first), 100)}" if first else status
        extra = [f"run {p['child_run_id']}"] if p.get("child_run_id") else []
        return [line, *extra], is_error or status not in ("succeeded",)
    if isinstance(p.get("http_status"), int) and p["http_status"] >= 400:
        is_error = True
    if name == "bash":
        b = parse_bash(output)
        lines = _lines(b["stdout"]) + _lines(b["stderr"])
        head = lines[:PREVIEW_LINES] or ["(No content)"]
        if len(lines) > PREVIEW_LINES:
            head.append(f"… +{len(lines) - PREVIEW_LINES} lines")
        failed = is_error or (b["exit_code"] not in (0, None))
        if b["timed_out"]:
            head.append("Timed out")
        if failed and b["exit_code"] not in (0, None):
            head.insert(0, f"Exit code {b['exit_code']}")
        return head, failed
    if is_error:
        lines = _lines(output) or ["(error)"]
        return lines[:PREVIEW_LINES] + ([f"… +{len(lines) - PREVIEW_LINES} lines"] if len(lines) > PREVIEW_LINES else []), True
    if name == "read_file":
        n = 0 if output.strip() == "(empty file)" else len(_lines(output))
        return [f"Read {_plural(n, 'line')}"], False
    if name == "write_file":
        n = len(_lines(str(args.get("content", ""))))
        return [f"Wrote {_plural(n, 'line')} to {args.get('path', '')}"], False
    if name == "edit_file":
        a, r = line_diff_counts(str(args.get("old", "")), str(args.get("new", "")))
        return [f"Added {_plural(a, 'line')}, removed {_plural(r, 'line')}"], False
    lines = _lines(output) or ["(No content)"]
    extra = [f"… +{len(lines) - PREVIEW_LINES} lines"] if len(lines) > PREVIEW_LINES else []
    return lines[:PREVIEW_LINES] + extra, False


def fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class TranscriptPrinter:
    def __init__(self, stream: TextIO | None = None, color: bool | None = None) -> None:
        self.out = stream or sys.stdout
        self.color = use_color(self.out) if color is None else color
        self.calls: dict[str, dict[str, Any]] = {}

    def _c(self, code: str, s: str) -> str:
        return f"\x1b[{code}m{s}\x1b[0m" if self.color else s

    def _w(self, s: str = "") -> None:
        self.out.write(s + "\n")
        self.out.flush()

    def event(self, e: Event) -> None:
        p = e.payload
        if e.type == "assistant" and p.get("text") and p.get("kind") != "final":
            text = plain_markdown(str(p["text"])).split("\n")
            self._w()
            self._w(f"{self._c('1', '●')} {text[0]}")
            for line in text[1:]:
                self._w(f"  {line}")
        elif e.type == "tool_call":
            args = p.get("args") if isinstance(p.get("args"), dict) else {}
            self.calls[str(p.get("id"))] = {"name": p.get("name"), "args": args}
            label = tool_label(str(p.get("name")), args)
            tool, _, rest = label.partition("(")
            self._w()
            self._w(f"{self._c('32', '●')} {self._c('1', tool)}({rest}")
        elif e.type == "tool_result":
            call = self.calls.pop(str(p.get("id")), {"name": p.get("name"), "args": {}})
            lines, err = result_summary(str(call["name"]), call["args"], str(p.get("output", "")),
                                        bool(p.get("is_error")), p)
            for i, line in enumerate(lines):
                prefix = "  ⎿  " if i == 0 else "     "
                self._w(self._c("31", prefix + line) if err else self._c("2", prefix + line))
        elif e.type == "error":
            self._w()
            self._w(self._c("31", f"● Error: {p.get('message', '')}"))

    def summary(self, status: str, seconds: float, turns: int, tokens: int, run_id: str) -> None:
        verb = "Done in" if status == "succeeded" else f"{status.capitalize()} after"
        parts = [f"{verb} {seconds:.1f}s", _plural(turns, "turn")]
        if tokens:
            parts.append(f"{fmt_tokens(tokens)} tokens")
        parts.append(f"run {run_id}")
        line = "✻ " + " · ".join(parts)
        self._w()
        self._w(self._c("1;32" if status == "succeeded" else "1;31", line))

    def final(self, text: str) -> None:
        if text:
            self._w()
            self._w(plain_markdown(text))
