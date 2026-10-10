"""Claude Code style transcript rendering for the terminal, with plain ANSI escapes and no extra dependencies.

The one terminal renderer, used by `forge run` (local runs) and `forge chat` (sessions). Events are the
JSON objects the server streams (`{run_id, seq, ts, type, payload}`) or `forge.types.Event` models; unknown
event types are ignored so newer servers keep working. Color follows NO_COLOR and whether the output is a
terminal; the live "Working" status line is drawn only on terminals.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import threading
import time
from typing import Any, TextIO

PREVIEW_LINES = 3

# ---------------------------------------------------------------- styling


def color_enabled(stream: TextIO) -> bool:
    if "NO_COLOR" in os.environ:  # https://no-color.org: any value disables color
        return False
    force = os.environ.get("FORCE_COLOR")
    if force:  # like Node and chalk: FORCE_COLOR=0 or false turns color off, any other value forces it on
        return force.lower() not in ("0", "false")
    return is_tty(stream) and os.environ.get("TERM") != "dumb"


def is_tty(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


class Style:
    CODES = {"bold": "1", "dim": "2", "italic": "3", "red": "31", "green": "32", "yellow": "33", "blue": "34",
             "magenta": "35", "cyan": "36", "gray": "90"}

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles or not text:
            return text
        codes = ";".join(self.CODES[s] for s in styles)
        return f"\033[{codes}m{text}\033[0m"


# ---------------------------------------------------------------- markdown cleanup

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((\S+?)\)")


def clean_markdown(text: str, style: Style) -> str:
    """Light markdown for terminals: headings and bold become bold, inline code is colored, bullets become
    dots, links show their URL, and code fences are dropped with the code indented."""
    out: list[str] = []
    in_code = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            out.append("  " + style(line, "cyan"))
            continue
        if m := _HEADING.match(line):
            out.append(style(m.group(2), "bold"))
            continue
        if m := _BULLET.match(line):
            line = f"{m.group(1)}• {m.group(2)}"
        line = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", line)
        line = _BOLD.sub(lambda m: style(m.group(1) or m.group(2), "bold"), line)
        line = _CODE.sub(lambda m: style(m.group(1), "cyan"), line)
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------- tool headers and summaries

TOOL_TITLES = {"bash": "Bash", "read_file": "Read", "write_file": "Write", "edit_file": "Update"}


def _one_line(s: str, limit: int = 120) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def tool_header(name: str, args: dict[str, Any]) -> str:
    """`Bash(python hello.py)`, `Update(src/app.py)`, `Agent(fixer)`, `github.list_issues(repo=a/b)`, or
    `name(k=v, ...)` for other tools."""
    title = TOOL_TITLES.get(name, name)
    if name == "bash":
        lines = str(args.get("cmd") or args.get("command") or "").strip().split("\n")
        arg = lines[0] + (" …" if len(lines) > 1 else "")
    elif name in ("read_file", "write_file", "edit_file"):
        arg = args.get("path", "")
        if name == "read_file" and (args.get("start") or args.get("end")):
            arg += f":{args.get('start') or 1}-{args.get('end') or ''}".rstrip("-")
    elif name.startswith("agent__"):
        return f"Agent({name.removeprefix('agent__')})"
    else:
        if name.startswith("mcp__") and name.count("__") >= 2:
            _, server, tool = name.split("__", 2)
            title = f"{server}.{tool}"
        arg = ", ".join(f"{k}={json.dumps(v) if not isinstance(v, str) else v}" for k, v in args.items())
    return f"{title}({_one_line(arg)})"


_BASH = re.compile(r"^exit_code: (-?\d+)\n--- stdout ---\n(.*?)\n--- stderr ---\n(.*)$", re.DOTALL)
_TIMED_OUT = re.compile(r"\n?\[command timed out after (\d+)s and was killed\]\s*$")


def parse_bash(output: str) -> dict[str, Any]:
    """Split forge's bash tool output into exit code, stdout, stderr and the timed-out flag."""
    timed_out = bool(_TIMED_OUT.search(output))
    body = _TIMED_OUT.sub("", output)
    m = _BASH.match(body)
    if not m:
        return {"exit_code": None, "stdout": body, "stderr": "", "timed_out": timed_out}
    return {"exit_code": int(m.group(1)), "stdout": m.group(2), "stderr": m.group(3), "timed_out": timed_out}


def line_diff_counts(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for d in difflib.ndiff(old.rstrip("\n").splitlines(), new.rstrip("\n").splitlines()):
        if d.startswith("+ "):
            added += 1
        elif d.startswith("- "):
            removed += 1
    return added, removed


def fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{n:,}"


def preview(text: str, n: int = PREVIEW_LINES) -> tuple[list[str], int]:
    """First n non-trailing lines and how many more there are."""
    lines = str(text).rstrip("\n").splitlines()
    return lines[:n], max(0, len(lines) - n)


# ---------------------------------------------------------------- transcript


class Transcript:
    """Writes one turn's events as a Claude Code like transcript."""

    def __init__(self, out: TextIO | None = None, *, color: bool | None = None, tty: bool | None = None,
                 clock: Any = time.monotonic) -> None:
        self.out = out or sys.stdout
        self.style = Style(color_enabled(self.out) if color is None else color)
        self.tty = is_tty(self.out) if tty is None else tty
        self.clock = clock
        self.calls: dict[str, dict] = {}
        self.last_text = ""
        self.lock = threading.RLock()
        self._status_on = False
        self._started: float | None = None
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None
        self.status_label = "Working…"
        self.run_id: str | None = None  # shown in the footer when set (forge run)

    # ------------------------------------------------ output primitives

    def write(self, text: str = "") -> None:
        with self.lock:
            self._clear_status()
            self.out.write(text + "\n")
            self._draw_status()
            self.out.flush()

    def info(self, text: str) -> None:
        self.write(self.style(text, "dim"))

    def error(self, text: str) -> None:
        self.write(self.style(text, "red"))

    def _status_text(self) -> str:
        secs = int(self.clock() - (self._started or self.clock()))
        return self.style("✻ ", "magenta") + self.style(f"{self.status_label} {secs}s", "magenta") + \
            self.style("  (ctrl+c to interrupt)", "dim")

    def _clear_status(self) -> None:
        if self._status_on:
            self.out.write("\r\033[K")
            self._status_on = False

    def _draw_status(self) -> None:
        if self.tty and self._started is not None and not self._stop.is_set():
            self.out.write("\r\033[K" + self._status_text())
            self._status_on = True

    # ------------------------------------------------ turn lifecycle

    def start_turn(self) -> None:
        """Starts the live status line (terminals only) and resets per-turn state."""
        self.calls.clear()
        self.last_text = ""
        self._started = self.clock()
        self._stop.clear()
        if self.tty:
            with self.lock:
                self._draw_status()
                self.out.flush()
            self._ticker = threading.Thread(target=self._tick, daemon=True)
            self._ticker.start()

    def _tick(self) -> None:
        while not self._stop.wait(0.5):
            with self.lock:
                self._draw_status()
                self.out.flush()

    def end_turn(self) -> None:
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join(timeout=2)
            self._ticker = None
        with self.lock:
            self._clear_status()
            self.out.flush()
        self._started = None

    # ------------------------------------------------ events

    def event(self, e: Any) -> None:
        if hasattr(e, "model_dump"):  # forge.types.Event from a local run
            e = e.model_dump()
        handler = getattr(self, f"_on_{e.get('type')}", None)
        if handler is not None:
            handler(e.get("payload") or {})

    def _on_assistant(self, p: dict) -> None:
        text = str(p.get("text") or "").strip()
        if not text:
            return
        self.last_text = text
        self.write(self._bullet(clean_markdown(text, self.style)))
        self.write()

    def _bullet(self, text: str, color: str | None = None) -> str:
        lines = text.splitlines() or [""]
        dot = self.style("●", color) if color else "●"
        return "\n".join([f"{dot} {lines[0]}"] + [f"  {ln}" for ln in lines[1:]])

    def _on_tool_call(self, p: dict) -> None:
        name, args = str(p.get("name") or "tool"), p.get("args") or {}
        if not isinstance(args, dict):
            args = {"args": args}
        self.calls[str(p.get("id"))] = {"name": name, "args": args}
        head = tool_header(name, args)
        title, _, rest = head.partition("(")
        self.write(self.style("●", "green") + " " + self.style(title, "bold") + "(" + rest)
        if name == "edit_file":
            self._diff(str(args.get("old") or ""), str(args.get("new") or ""))

    def _diff(self, old: str, new: str) -> None:
        """Update shows the edit as removed and added lines (first lines of each, then a count)."""
        rows: list[str] = []
        for sign, text, color in (("-", old, "red"), ("+", new, "green")):
            lines, more = preview(text, 6)
            rows += [self.style(f"{sign} {ln}", color) for ln in lines]
            if more:
                rows.append(self.style(f"  … +{more} lines", "dim"))
        self._result_block(rows)

    def _result_block(self, rows: list[str]) -> None:
        for i, row in enumerate(rows):
            lead = "  ⎿  " if i == 0 else "     "
            self.write(self.style(lead, "dim") + row)

    def _on_tool_result(self, p: dict) -> None:
        call = self.calls.get(str(p.get("id"))) or {"name": p.get("name"), "args": {}}
        name, args = str(call["name"]), call["args"]
        output = str(p.get("output") or "")
        if name.startswith("agent__") or p.get("child_run_id"):  # a delegated agent: its status and answer
            status = str(p.get("child_status") or ("failed" if p.get("is_error") else "succeeded"))
            first = next((ln.strip() for ln in output.splitlines() if ln.strip()), "")
            color = "dim" if status == "succeeded" else "red"
            rows = [self.style(f"{status}: {_one_line(first, 100)}" if first else status, color)]
            if p.get("child_run_id"):
                rows.append(self.style(f"run {p['child_run_id']}", "dim"))
            self._result_block(rows)
            self.write()
            return
        if name == "bash":
            b = parse_bash(output)
            if b["exit_code"] is not None:
                failed = bool(p.get("is_error")) or b["exit_code"] != 0
                body = (b["stdout"].rstrip("\n") + ("\n" + b["stderr"] if b["stderr"].strip() else "")).strip("\n")
                lines, more = preview(body)
                tone = "red" if failed else "dim"
                rows = ([self.style(f"Exit code {b['exit_code']}", "red")] if failed else [])
                rows += [self.style(ln, tone) for ln in lines]
                if not lines and not failed:
                    rows.append(self.style("(no output)", "dim"))
                if more:
                    rows.append(self.style(f"… +{more} lines", "dim"))
                if b["timed_out"]:
                    rows.append(self.style("Timed out", "red"))
                self._result_block(rows)
                self.write()
                return
        if isinstance(p.get("http_status"), int) and p["http_status"] >= 400:
            p = {**p, "is_error": True}
        if p.get("is_error"):
            lines, more = preview(output or "error")
            rows = [self.style(ln, "red") for ln in lines]
            if more:
                rows.append(self.style(f"… +{more} lines", "dim"))
            self._result_block(rows)
            self.write()
            return
        if name == "edit_file":
            self.write()  # the diff was shown with the call
            return
        if name == "write_file":
            content = str(args.get("content") or "")
            n = len(content.splitlines())
            rows = [self.style(f"Wrote {n} {'line' if n == 1 else 'lines'} to {args.get('path', '')}", "dim")]
            lines, more = preview(content)
            rows += [self.style(ln, "dim") for ln in lines]
            if more:
                rows.append(self.style(f"… +{more} lines", "dim"))
        elif name == "read_file":
            n = len(output.splitlines())
            rows = [self.style(f"Read {n} {'line' if n == 1 else 'lines'}", "dim")]
        else:
            lines, more = preview(output)
            rows = [self.style(ln, "dim") for ln in lines] or [self.style("(no output)", "dim")]
            if more:
                rows.append(self.style(f"… +{more} lines", "dim"))
        self._result_block(rows)
        self.write()

    def _on_error(self, p: dict) -> None:
        self.write(self._bullet(self.style(f"Error: {p.get('message') or 'unknown error'}", "red"), "red"))

    def _on_run_finished(self, p: dict) -> None:
        output = str(p.get("output") or "").strip()
        if output and output != self.last_text:
            self.write(self._bullet(clean_markdown(output, self.style)))
            self.write()
        self.footer(p)

    def footer(self, p: dict, run_id: str | None = None) -> None:
        status = str(p.get("status") or "")
        parts = [status] if status else []
        if p.get("turns"):
            parts.append(f"{p['turns']} turns")
        if self._started is not None:
            parts.append(f"{self.clock() - self._started:.1f}s")
        tokens = int(p.get("tokens_in") or 0) + int(p.get("tokens_out") or 0)
        if tokens:
            parts.append(f"{fmt_tokens(tokens)} tokens")
        if run_id or self.run_id:
            parts.append(f"run {run_id or self.run_id}")
        text = " · ".join(parts)
        if status == "succeeded":
            self.write(self.style("✓ ", "green") + self.style(text, "dim"))
        elif status == "cancelled":
            self.write(self.style("■ ", "yellow") + self.style(text, "dim"))
        else:
            self.write(self.style("✗ " + text, "red"))

    def interrupted(self) -> None:
        self.write(self.style("■ Interrupted. ", "yellow") + self.style("Stopped the turn; send a new message.", "dim"))

    # ------------------------------------------------ static views used by slash commands

    def files(self, files: list[dict]) -> None:
        if not files:
            self.info("No files in this turn's workspace.")
            return
        for f in files:
            mark = {"added": "A", "modified": "M", "renamed": "R"}.get(str(f.get("change") or ""), " ")
            if f.get("changed") and mark == " ":
                mark = "M"
            size = _fmt_size(int(f.get("size") or 0))
            path = f["path"] if f.get("is_text", True) else f"{f['path']} (binary)"
            line = f"{mark} {path}"
            if f.get("changed"):
                self.write(self.style(line, "green") + "  " + self.style(size, "dim"))
            else:
                self.write(line + "  " + self.style(size, "dim"))

    def file(self, path: str, text: str) -> None:
        lines = text.splitlines() or [""]
        width = len(str(len(lines)))
        self.write(self.style(path, "bold"))
        for i, ln in enumerate(lines, 1):
            self.write(self.style(str(i).rjust(width), "dim") + "  " + ln)


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"
