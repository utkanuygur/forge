"""`forge run` transcript: the same renderer as `forge chat` (forge/cli_render.py), fed forge.types.Event models."""

from __future__ import annotations

import io

from forge.cli_render import Transcript, fmt_tokens, line_diff_counts, parse_bash, tool_header
from forge.types import Event


def ev(seq: int, type_: str, **payload) -> Event:
    return Event(run_id="r", seq=seq, ts=0.0, type=type_, payload=payload)


def bash_out(code: int, stdout: str, stderr: str = "") -> str:
    return f"exit_code: {code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"


class Clock:
    t = 0.0

    def __call__(self) -> float:
        return self.t


def render(events, color=False, run_id: str | None = None) -> str:
    buf = io.StringIO()
    t = Transcript(buf, color=color, tty=False, clock=Clock())
    t.run_id = run_id
    for e in events:
        t.event(e)
    return buf.getvalue()


def test_local_run_events_render_like_chat():
    out = render([
        ev(0, "assistant", text="I'll create **hello.py**.", kind="progress"),
        ev(1, "tool_call", id="a", name="write_file", args={"path": "hello.py", "content": "a\nb\n"}),
        ev(2, "tool_result", id="a", name="write_file", output="wrote 4 chars", is_error=False),
        ev(3, "tool_call", id="b", name="bash", args={"cmd": "python hello.py\necho more"}),
        ev(4, "tool_result", id="b", name="bash", output=bash_out(0, "1\n2\n3\n4\n5\n"), is_error=False),
        ev(5, "tool_call", id="d", name="read_file", args={"path": "x.py", "start": 2, "end": 3}),
        ev(6, "tool_result", id="d", name="read_file", output="2\tc\n3\td", is_error=False),
        ev(7, "assistant", text="final answer", kind="final"),
        ev(8, "run_finished", status="succeeded", output="final answer", turns=5, tokens_in=3000, tokens_out=200),
    ], run_id="r_abc")
    assert "● I'll create hello.py." in out
    assert "● Write(hello.py)\n  ⎿  Wrote 2 lines to hello.py" in out
    assert "● Bash(python hello.py …)\n  ⎿  1\n     2\n     3\n     … +2 lines" in out  # stdout, not the raw format
    assert "● Read(x.py:2-3)\n  ⎿  Read 2 lines" in out
    assert out.count("final answer") == 1
    assert out.rstrip().endswith("✓ succeeded · 5 turns · 3.2k tokens · run r_abc")  # no turn started: no time
    assert "\x1b[" not in out


def test_bash_exit_codes_errors_and_timeouts():
    out = render([
        ev(0, "tool_call", id="a", name="bash", args={"cmd": "false"}),
        ev(1, "tool_result", id="a", name="bash", output=bash_out(1, "", "boom"), is_error=True),
        ev(2, "tool_call", id="b", name="bash", args={"cmd": "sleep 9"}),
        ev(3, "tool_result", id="b", name="bash", output=bash_out(-9, "") + "\n[command timed out after 5s and was killed]",
           is_error=True),
        ev(4, "error", message="model error: x"),
    ], color=True)
    assert "\x1b[31mExit code 1\x1b[0m" in out and "\x1b[31mboom\x1b[0m" in out
    assert "Timed out" in out and "Exit code -9" in out
    assert "\x1b[31mError: model error: x\x1b[0m" in out


def test_delegated_agent_mcp_and_http_lines():
    out = render([
        ev(0, "tool_call", id="a", name="agent__fixer", args={"input": "fix it"}),
        ev(1, "tool_result", id="a", name="agent__fixer", output="**Fixed** the bug.\nmore", is_error=False,
           child_run_id="r_child", child_agent="fixer", child_status="succeeded"),
        ev(2, "tool_call", id="m", name="mcp__github__list_issues", args={"repo": "a/b"}),
        ev(3, "tool_result", id="m", name="mcp__github__list_issues", output="#1", is_error=False, source="mcp"),
        ev(4, "tool_call", id="h", name="get_order", args={"id": "7"}),
        ev(5, "tool_result", id="h", name="get_order", output="HTTP 404 Not Found", is_error=False, source="http",
           http_status=404),
    ], color=True)
    plain = out.replace("\x1b[2m", "").replace("\x1b[31m", "").replace("\x1b[0m", "").replace("\x1b[1m", "") \
        .replace("\x1b[32m", "")
    assert "● Agent(fixer)\n  ⎿  succeeded: **Fixed** the bug.\n     run r_child" in plain
    assert "● github.list_issues(repo=a/b)\n  ⎿  #1" in plain
    assert "\x1b[31mHTTP 404 Not Found\x1b[0m" in out  # an HTTP error status is shown as an error


def test_helpers():
    assert parse_bash(bash_out(2, "o", "e") + "\n[command timed out after 5s and was killed]") == {
        "exit_code": 2, "stdout": "o", "stderr": "e", "timed_out": True}
    assert tool_header("bash", {"cmd": "x" * 200}).endswith("…)")
    assert tool_header("agent__a", {"input": "x"}) == "Agent(a)"
    assert line_diff_counts("a\nb\n", "a\nc\nd\n") == (2, 1)
    assert fmt_tokens(3200) == "3.2k" and fmt_tokens(15) == "15"
