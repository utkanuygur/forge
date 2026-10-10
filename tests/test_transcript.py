"""CLI transcript rendering (forge/transcript.py)."""

from __future__ import annotations

import io

from forge.transcript import TranscriptPrinter, parse_bash, plain_markdown, tool_label, use_color
from forge.types import Event


def ev(seq: int, type_: str, **payload) -> Event:
    return Event(run_id="r", seq=seq, ts=0.0, type=type_, payload=payload)


def bash_out(code: int, stdout: str, stderr: str = "") -> str:
    return f"exit_code: {code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"


def render(events, color=False) -> str:
    buf = io.StringIO()
    p = TranscriptPrinter(buf, color=color)
    for e in events:
        p.event(e)
    return buf.getvalue()


def test_transcript_style():
    out = render([
        ev(0, "assistant", text="I'll create **hello.py**.", kind="progress"),
        ev(1, "tool_call", id="a", name="write_file", args={"path": "hello.py", "content": "a\nb\n"}),
        ev(2, "tool_result", id="a", name="write_file", output="wrote 4 chars", is_error=False),
        ev(3, "tool_call", id="b", name="bash", args={"cmd": "python hello.py\necho more"}),
        ev(4, "tool_result", id="b", name="bash", output=bash_out(0, "1\n2\n3\n4\n5\n"), is_error=False),
        ev(5, "tool_call", id="c", name="edit_file", args={"path": "x.py", "old": "a\nb\n", "new": "a\nc\nd\n"}),
        ev(6, "tool_result", id="c", name="edit_file", output="edited x.py", is_error=False),
        ev(7, "tool_call", id="d", name="read_file", args={"path": "x.py", "start": 2, "end": 3}),
        ev(8, "tool_result", id="d", name="read_file", output="2\tc\n3\td", is_error=False),
        ev(9, "assistant", text="final answer", kind="final"),
    ])
    assert "● I'll create hello.py." in out
    assert "● Write(hello.py)\n  ⎿  Wrote 2 lines to hello.py" in out
    assert "● Bash(python hello.py …)\n  ⎿  1\n     2\n     3\n     … +2 lines" in out
    assert "● Update(x.py)\n  ⎿  Added 2 lines, removed 1 line" in out
    assert "● Read(x.py:2-3)\n  ⎿  Read 2 lines" in out
    assert "final answer" not in out  # printed once by `final`, after the summary
    assert "\x1b[" not in out


def test_errors_are_red_and_exit_code_shown():
    out = render([
        ev(0, "tool_call", id="a", name="bash", args={"cmd": "false"}),
        ev(1, "tool_result", id="a", name="bash", output=bash_out(1, "", "boom"), is_error=True),
        ev(2, "error", message="model error: x"),
    ], color=True)
    assert "\x1b[31m  ⎿  Exit code 1\x1b[0m" in out and "boom" in out
    assert "\x1b[31m● Error: model error: x\x1b[0m" in out


def test_summary_and_final():
    buf = io.StringIO()
    p = TranscriptPrinter(buf, color=False)
    p.summary("succeeded", 16.52, 5, 3200, "r_abc")
    p.final("## Summary\nAdded **/health** with `pytest` coverage.")
    out = buf.getvalue()
    assert "✻ Done in 16.5s · 5 turns · 3.2k tokens · run r_abc" in out
    assert "Summary\nAdded /health with pytest coverage." in out


def test_helpers(monkeypatch):
    assert plain_markdown("# T\n**b** and `c`") == "T\nb and c"
    assert parse_bash(bash_out(2, "o", "e") + "\n[command timed out after 5s and was killed]") == {
        "exit_code": 2, "stdout": "o", "stderr": "e", "timed_out": True}
    assert tool_label("bash", {"cmd": "x" * 200}).endswith("…)")
    assert tool_label("http_get", {"q": 1}) == 'http_get({"q": 1})'

    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert use_color(Tty()) and not use_color(io.StringIO())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not use_color(Tty())
