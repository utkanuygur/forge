"""`forge chat`: transcript rendering of every event type and the REPL loop against a mock sessions API."""

from __future__ import annotations

import io
import json
import re
from typing import Any, Iterator

import httpx
import pytest

from forge import cli
from forge.cli_render import Style, Transcript, clean_markdown, color_enabled, tool_header

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r")


def plain(s: str) -> str:
    return ANSI.sub("", s)


def ev(type_: str, seq: int = 0, **payload: Any) -> dict:
    return {"run_id": "r_1", "seq": seq, "ts": 0.0, "type": type_, "payload": payload}


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def transcript(color: bool = False, tty: bool = False) -> tuple[Transcript, io.StringIO]:
    out = io.StringIO()
    return Transcript(out, color=color, tty=tty, clock=Clock()), out


# ---------------------------------------------------------------- rendering


def test_assistant_narration_with_markdown_cleanup():
    t, out = transcript()
    t.event(ev("assistant", text="## Plan\nI will **fix** the `bug`:\n- step one\n* step two\n```py\nx = 1\n```\n"
                                 "See [docs](http://x.io)"))
    assert out.getvalue() == ("● Plan\n  I will fix the bug:\n  • step one\n  • step two\n    x = 1\n"
                              "  See docs (http://x.io)\n\n")


def test_markdown_cleanup_uses_ansi_when_colored():
    s = clean_markdown("**b** and `c`", Style(True))
    assert s == "\x1b[1mb\x1b[0m and \x1b[36mc\x1b[0m"


def test_bash_call_and_result_preview_with_more_lines():
    t, out = transcript()
    t.event(ev("tool_call", id="c1", name="bash", args={"cmd": "pytest -q"}))
    t.event(ev("tool_result", id="c1", name="bash", output="l1\nl2\nl3\nl4\nl5\n", is_error=False))
    assert out.getvalue() == "● Bash(pytest -q)\n  ⎿  l1\n     l2\n     l3\n     … +2 lines\n\n"


def test_empty_output_and_long_command_header():
    t, out = transcript()
    t.event(ev("tool_call", id="c1", name="bash", args={"cmd": "echo " + "x" * 300}))
    t.event(ev("tool_result", id="c1", name="bash", output="", is_error=False))
    first, second = out.getvalue().splitlines()[:2]
    assert first.startswith("● Bash(echo xxx") and first.endswith("…)") and len(first) < 140
    assert second == "  ⎿  (no output)"


def test_error_result_is_red():
    t, out = transcript(color=True)
    t.event(ev("tool_call", id="c1", name="bash", args={"cmd": "false"}))
    t.event(ev("tool_result", id="c1", name="bash", output="exit 1\nboom", is_error=True))
    text = out.getvalue()
    assert "\x1b[31mexit 1\x1b[0m" in text and "\x1b[31mboom\x1b[0m" in text
    assert plain(text) == "● Bash(false)\n  ⎿  exit 1\n     boom\n\n"


def test_update_shows_diff_lines():
    t, out = transcript(color=True)
    t.event(ev("tool_call", id="c1", name="edit_file", args={"path": "app.py", "old": "a = 1\nb = 2", "new": "a = 3"}))
    t.event(ev("tool_result", id="c1", name="edit_file", output="ok", is_error=False))
    text = out.getvalue()
    assert plain(text) == "● Update(app.py)\n  ⎿  - a = 1\n     - b = 2\n     + a = 3\n\n"
    assert "\x1b[31m- a = 1\x1b[0m" in text and "\x1b[32m+ a = 3\x1b[0m" in text


def test_write_and_read_summaries():
    t, out = transcript()
    t.event(ev("tool_call", id="w", name="write_file", args={"path": "hello.py", "content": "a\nb\nc\nd\n"}))
    t.event(ev("tool_result", id="w", name="write_file", output="ok", is_error=False))
    t.event(ev("tool_call", id="r", name="read_file", args={"path": "hello.py", "start": 2, "end": 3}))
    t.event(ev("tool_result", id="r", name="read_file", output="2\tb\n3\tc", is_error=False))
    assert out.getvalue() == ("● Write(hello.py)\n  ⎿  Wrote 4 lines to hello.py\n     a\n     b\n     c\n"
                              "     … +1 lines\n\n● Read(hello.py:2-3)\n  ⎿  Read 2 lines\n\n")


def test_other_tools_and_unknown_events():
    assert tool_header("lookup", {"q": "x", "n": 2}) == "lookup(q=x, n=2)"
    t, out = transcript()
    t.event(ev("run_started", model="fake:hello", input="go"))
    t.event(ev("something_new", x=1))
    assert out.getvalue() == ""


def test_error_event_and_final_answer_footer():
    t, out = transcript()
    t.start_turn()
    t.clock.t += 2.5
    t.event(ev("assistant", text="Done."))
    t.event(ev("error", message="Model declined: policy"))
    t.event(ev("run_finished", status="failed", output="Done.", turns=2, tokens_in=10, tokens_out=5))
    t.end_turn()
    assert out.getvalue() == "● Done.\n\n● Error: Model declined: policy\n✗ failed · 2 turns · 2.5s · 15 tokens\n"


def test_final_output_printed_when_it_differs_from_narration():
    t, out = transcript()
    t.event(ev("assistant", text="Working on it"))
    t.event(ev("run_finished", status="succeeded", output="**All** done", turns=1))
    assert out.getvalue() == "● Working on it\n\n● All done\n\n✓ succeeded · 1 turns\n"


def test_cancelled_footer():
    t, out = transcript()
    t.footer({"status": "cancelled"})
    assert out.getvalue() == "■ cancelled\n"


def test_status_line_only_on_tty():
    t, out = transcript(tty=True)
    t.start_turn()
    t.clock.t += 12
    t.write("hello")
    t.end_turn()
    text = out.getvalue()
    assert "✻ Working… 12s" in text and "\r\x1b[K" in text
    assert text.endswith("\r\x1b[K")  # the status line is cleared when the turn ends
    t2, out2 = transcript(tty=False)
    t2.start_turn()
    t2.write("hello")
    t2.end_turn()
    assert out2.getvalue() == "hello\n"


def test_no_color_and_non_tty(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert color_enabled(Tty()) is True
    assert color_enabled(io.StringIO()) is False
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert color_enabled(io.StringIO()) is True
    monkeypatch.setenv("FORCE_COLOR", "0")  # what scripts/test_fast.py exports to its layers
    assert color_enabled(io.StringIO()) is False and color_enabled(Tty()) is False
    monkeypatch.delenv("FORCE_COLOR")
    monkeypatch.setenv("NO_COLOR", "")
    assert color_enabled(Tty()) is False
    t = Transcript(Tty())
    assert t.style.enabled is False and t.tty is True


def test_files_and_file_views():
    t, out = transcript()
    t.files([{"path": "hello.py", "size": 26, "is_text": True, "changed": True, "change": "added"},
             {"path": "README.md", "size": 2048, "is_text": True, "changed": False},
             {"path": "logo.png", "size": 10, "is_text": False, "changed": False}])
    t.file("hello.py", "a\nb\n")
    assert out.getvalue() == ("A hello.py  26 B\n  README.md  2.0 KB\n  logo.png (binary)  10 B\n"
                              "hello.py\n1  a\n2  b\n")


# ---------------------------------------------------------------- REPL against a mock sessions API


def sse(events: list[dict], *, interrupt_after: int | None = None) -> Iterator[bytes]:
    for i, e in enumerate(events):
        if interrupt_after is not None and i == interrupt_after:
            raise KeyboardInterrupt  # what the terminal delivers on Ctrl+C mid stream
        yield f"event: {e['type']}\nid: {e['seq']}\ndata: {json.dumps(e)}\n\n".encode()
        yield b": ping\n\n"
    if interrupt_after is None:
        yield b"event: end\ndata: {}\n\n"
    else:
        raise KeyboardInterrupt


def hello_events(run_id: str) -> list[dict]:
    raw = [("run_started", {"model": "fake:hello", "input": "go"}),
           ("tool_call", {"id": "a", "name": "write_file", "args": {"path": "hello.py", "content": "print('hi')\n"}}),
           ("tool_result", {"id": "a", "name": "write_file", "output": "wrote", "is_error": False}),
           ("tool_call", {"id": "b", "name": "bash", "args": {"cmd": "python hello.py"}}),
           ("tool_result", {"id": "b", "name": "bash", "output": "hi\n", "is_error": False}),
           ("assistant", {"text": "Created and ran hello.py."}),
           ("run_finished", {"status": "succeeded", "output": "Created and ran hello.py.", "turns": 3})]
    return [{"run_id": run_id, "seq": i, "ts": 0.0, "type": t, "payload": p} for i, (t, p) in enumerate(raw)]


class FakeSessionsApi:
    """Implements the sessions contract used by `forge chat` in memory."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.busy = False
        self.drop_once = False

    def handler(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path.removeprefix("/v1"), req.method
        body = json.loads(req.content) if req.content else None
        self.calls.append((method, path + (f"?{req.url.query.decode()}" if req.url.query else ""), body))
        if m := re.fullmatch(r"/agents/([^/]+)/sessions", path):
            if method == "POST":
                if m.group(1) == "missing":
                    return httpx.Response(404, json={"detail": "agent 'missing' not found"})
                sid = f"s_{len(self.sessions) + 1}"
                s = {"id": sid, "agent_slug": m.group(1), "version_n": (body or {}).get("version"), "draft": None,
                     "title": "", "created_at": 0.0, "updated_at": 0.0}
                self.sessions[sid] = {**s, "turns": []}
                return httpx.Response(200, json=s)
        if m := re.fullmatch(r"/sessions/([^/]+)", path):
            s = self.sessions.get(m.group(1))
            return httpx.Response(200, json=s) if s else httpx.Response(404, json={"detail": "session not found"})
        if m := re.fullmatch(r"/sessions/([^/]+)/messages", path):
            if self.busy:
                return httpx.Response(409, json={"detail": "a turn is running"})
            s = self.sessions[m.group(1)]
            rid = f"r_{len(self.runs) + 1}"
            run = {"id": rid, "status": "running", "session_id": s["id"], "turn_index": len(s["turns"]),
                   "input": body["input"], "output": ""}
            self.runs[rid] = run
            s["turns"].append(run)
            return httpx.Response(200, json=run)
        if m := re.fullmatch(r"/runs/([^/]+)/events", path):
            run = self.runs[m.group(1)]
            evs = hello_events(run["id"])
            after = int(req.url.params.get("after", "-1"))
            if run["input"] == "slow":
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      content=sse(evs[:4], interrupt_after=2))
            if self.drop_once:
                self.drop_once = False
                parts = [f"event: {e['type']}\nid: {e['seq']}\ndata: {json.dumps(e)}\n\n".encode() for e in evs[:2]]

                def dropping() -> Iterator[bytes]:
                    yield from parts
                    raise httpx.ReadError("connection reset")
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=dropping())
            run["status"], run["output"] = "succeeded", "Created and ran hello.py."
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=sse([e for e in evs if e["seq"] > after]))
        if m := re.fullmatch(r"/runs/([^/]+)/cancel", path):
            run = self.runs[m.group(1)]
            run["status"] = "cancelled"
            return httpx.Response(200, json=run)
        if m := re.fullmatch(r"/runs/([^/]+)", path):
            return httpx.Response(200, json=self.runs[m.group(1)])
        if m := re.fullmatch(r"/runs/([^/]+)/files", path):
            return httpx.Response(200, json=[
                {"path": "hello.py", "size": 12, "is_text": True, "changed": True, "change": "added"},
                {"path": "logo.png", "size": 4, "is_text": False, "changed": False}])
        if m := re.fullmatch(r"/runs/([^/]+)/files/(.+)", path):
            p = m.group(2)
            if p == "hello.py":
                return httpx.Response(200, text="print('hi')\n")
            if p == "logo.png":
                return httpx.Response(415, json={"detail": "binary"})
            return httpx.Response(404, json={"detail": f"{p} not found in the workspace"})
        return httpx.Response(404, json={"detail": "no route"})


class Keys:
    """Scripted prompt input: strings are typed lines; EOFError / KeyboardInterrupt are raised."""

    def __init__(self, *items: Any) -> None:
        self.items = list(items)
        self.prompts = 0

    def __call__(self, prompt: str) -> str:
        self.prompts += 1
        if not self.items:
            raise EOFError
        item = self.items.pop(0)
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item
        return item


def run_chat(*keys: Any, slug: str = "bot", version: int | None = None, session: str | None = None,
             fake: FakeSessionsApi | None = None) -> tuple[str, FakeSessionsApi, Keys]:
    fake = fake or FakeSessionsApi()
    client = httpx.Client(base_url="http://forge.test/v1", transport=httpx.MockTransport(fake.handler))
    out = io.StringIO()
    k = Keys(*keys)
    cli.Chat(client, slug, version, Transcript(out, color=False, tty=False), k).run(session)
    return out.getvalue(), fake, k


def test_parse_target():
    assert cli.parse_target("bot") == ("bot", None)
    assert cli.parse_target("bot@3") == ("bot", 3)
    assert cli.parse_target("bot@v12") == ("bot", 12)
    with pytest.raises(Exception):
        cli.parse_target("bot@latest")


def test_repl_creates_session_streams_turn_and_exits_on_ctrl_d():
    text, fake, keys = run_chat("", "write hello", EOFError, version=2)
    assert fake.calls[0] == ("POST", "/agents/bot/sessions", {"version": 2})
    assert ("POST", "/sessions/s_1/messages", {"input": "write hello"}) in fake.calls
    assert ("GET", "/runs/r_1/events?after=-1", None) in fake.calls
    assert "Forge chat · bot v2 · session s_1" in text
    assert ("● Write(hello.py)\n  ⎿  Wrote 1 line to hello.py\n     print('hi')\n\n● Bash(python hello.py)\n"
            "  ⎿  hi\n\n● Created and ran hello.py.\n\n✓ succeeded · 3 turns") in text
    assert text.count("Created and ran hello.py.") == 1  # final answer is not repeated
    assert keys.prompts == 3


def test_ctrl_c_during_turn_cancels_then_second_ctrl_c_exits():
    text, fake, keys = run_chat("slow", KeyboardInterrupt, "never sent")
    assert ("POST", "/runs/r_1/cancel", None) in fake.calls
    assert fake.runs["r_1"]["status"] == "cancelled"
    assert "● Bash(python hello.py)" not in text and "● Write(hello.py)" in text
    assert "■ Interrupted." in text
    assert keys.items == ["never sent"]  # the second Ctrl+C left the loop
    assert len([c for c in fake.calls if c[1].endswith("/messages")]) == 1


def test_single_ctrl_c_at_prompt_only_warns():
    text, fake, keys = run_chat(KeyboardInterrupt, "/help", KeyboardInterrupt, KeyboardInterrupt, "unused")
    assert "Press Ctrl+C again to exit." in text and "/open <path>" in text
    assert keys.items == ["unused"]


def test_busy_session_reports_409():
    fake = FakeSessionsApi()
    fake.busy = True
    text, _, _ = run_chat("hi", fake=fake)
    assert "A turn is still running." in text


def test_slash_commands_new_files_open_and_unknown():
    text, fake, _ = run_chat("/files", "/open hello.py", "go", "/files", "/open hello.py", "/open logo.png",
                             "/open nope.txt", "/open", "/bogus", "/new", "/files", "/exit", "unused")
    assert text.count("No turns yet.") == 3  # /files and /open before any turn, and /files after /new
    assert "A hello.py  12 B\n  logo.png (binary)  4 B" in text
    assert "hello.py\n1  print('hi')" in text
    assert "logo.png is a binary file and is not shown." in text
    assert "nope.txt not found in the workspace" in text
    assert "Usage: /open <path>" in text and "Unknown command /bogus." in text
    assert "session s_2" in text
    assert ("GET", "/runs/r_1/files?changed=1", None) in fake.calls


def test_resume_session_shows_previous_turns_and_targets_its_latest_run():
    fake = FakeSessionsApi()
    fake.sessions["s_9"] = {"id": "s_9", "agent_slug": "bot", "version_n": None, "draft": None, "title": "t",
                            "created_at": 0.0, "updated_at": 0.0,
                            "turns": [{"id": "r_7", "input": "first", "output": "First answer"}]}
    fake.runs["r_7"] = fake.sessions["s_9"]["turns"][0]
    text, fake, _ = run_chat("/files", session="s_9", fake=fake)
    assert fake.calls[0] == ("GET", "/sessions/s_9", None)
    assert "bot prod · session s_9" in text and "> first\n● First answer" in text
    assert ("GET", "/runs/r_7/files?changed=1", None) in fake.calls


def test_reconnects_after_dropped_stream_with_after_seq():
    fake = FakeSessionsApi()
    fake.drop_once = True
    text, fake, _ = run_chat("go", fake=fake)
    streams = [c[1] for c in fake.calls if c[1].startswith("/runs/r_1/events")]
    assert streams == ["/runs/r_1/events?after=-1", "/runs/r_1/events?after=1"]
    assert text.count("● Write(hello.py)") == 1 and "✓ succeeded" in text


def test_startup_error_exits_nonzero():
    import typer

    with pytest.raises(typer.Exit):
        run_chat(slug="missing")
