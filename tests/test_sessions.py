"""Playground sessions: history reconstruction, truncation, archives, db migration and fake scenarios."""

from __future__ import annotations

import sqlite3

from forge import db
from forge.providers import ScenarioProvider
from forge.sessions import (
    TIGHT_TOOL_OUTPUT_CAP,
    build_history,
    read_archive,
    tar_bytes,
    turn_messages,
)
from forge.types import Message


def ev(seq: int, type_: str, **payload) -> dict:
    return {"seq": seq, "type": type_, "payload": payload}


TURN = [
    ev(0, "run_started"),
    ev(1, "assistant", text="I'll create hello.py.", kind="progress"),
    ev(2, "tool_call", id="a", name="write_file", args={"path": "hello.py", "content": "x"}),
    ev(3, "tool_result", id="a", name="write_file", output="wrote 1 chars", is_error=False),
    ev(4, "assistant", text="Now run it.", kind="progress"),
    ev(5, "tool_call", id="b", name="bash", args={"cmd": "python hello.py"}),
    ev(6, "tool_call", id="c", name="bash", args={"cmd": "ls"}),
    ev(7, "tool_result", id="b", name="bash", output="hi", is_error=False),
    ev(8, "tool_result", id="c", name="bash", output="hello.py", is_error=False),
    ev(9, "assistant", text="Done.", kind="final"),
    ev(10, "run_finished"),
]


def shape(msgs: list[Message]) -> list[tuple]:
    return [(m.role, m.text, [c.id for c in m.tool_calls], m.tool_call_id) for m in msgs]


def test_turn_messages_reconstructs_calls_and_results():
    assert shape(turn_messages("make it", TURN)) == [
        ("user", "make it", [], None),
        ("assistant", "I'll create hello.py.", ["a"], None),
        ("tool", "wrote 1 chars", [], "a"),
        ("assistant", "Now run it.", ["b", "c"], None),  # parallel calls stay in one assistant message
        ("tool", "hi", [], "b"),
        ("tool", "hello.py", [], "c"),
        ("assistant", "Done.", [], None),
    ]


def test_cancelled_turn_drops_unanswered_calls_and_notes_the_stop():
    evs = [ev(0, "assistant", text="Sleeping.", kind="progress"),
           ev(1, "tool_call", id="z", name="bash", args={"cmd": "sleep 30"}),
           ev(2, "error", message="Stopped by user")]
    msgs = turn_messages("wait", evs, status="cancelled")
    assert shape(msgs) == [("user", "wait", [], None), ("assistant", "Sleeping.", [], None)]
    msgs = turn_messages("wait", [ev(1, "tool_call", id="z", name="bash", args={})], status="cancelled")
    assert msgs[-1].text == "(This turn stopped by the user before a final answer.)"


def test_legacy_assistant_events_without_kind():
    evs = [ev(0, "tool_call", id="a", name="bash", args={}), ev(1, "tool_result", id="a", output="o"),
           ev(2, "assistant", text="answer")]
    assert shape(turn_messages("q", evs))[-1] == ("assistant", "answer", [], None)


def test_build_history_truncates_tool_output_and_drops_oldest_turns():
    big = [ev(0, "tool_call", id="a", name="bash", args={}), ev(1, "tool_result", id="a", output="x" * 50_000),
           ev(2, "assistant", text="ok", kind="final")]
    msgs = turn_messages("q", big)
    assert len(msgs[2].text) < 2_200 and "omitted from history" in msgs[2].text

    turns = [(f"turn {i}", TURN, "succeeded") for i in range(5)]
    full = build_history(turns)
    assert sum(m.role == "user" for m in full) == 5
    small = build_history(turns, budget=300)
    users = [m.text for m in small if m.role == "user"]
    assert len(users) < 5 and users[-1] == "turn 4"
    assert users[0].startswith("[") and "earlier turn(s) of this session were omitted" in users[0]

    huge = build_history([("only", big, "succeeded")], budget=100)
    assert len(huge[2].text) <= TIGHT_TOOL_OUTPUT_CAP + 80


def test_archive_helpers(tmp_path):
    p = tmp_path / "ws.tar.gz"
    p.write_bytes(tar_bytes({"hello.py": b"print(1)\n", ".git/HEAD": b"ref", "src/a.txt": b"a"}))
    assert read_archive(str(p)) == {"hello.py": b"print(1)\n", "src/a.txt": b"a"}  # .git is never carried over


def test_migration_adds_session_columns_to_old_databases(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    old = sqlite3.connect(tmp_path / "forge.db")
    old.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, agent_id TEXT, version_id TEXT, source TEXT, input TEXT, "
                "status TEXT, output TEXT DEFAULT '', error TEXT, artifact_path TEXT, diff_path TEXT, "
                "turns INTEGER DEFAULT 0, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, "
                "eval_case_id TEXT, callback_url TEXT, created_at REAL, started_at REAL, ended_at REAL)")
    old.commit()
    old.close()
    db._initialized.clear()
    with db.connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
    assert {"session_id", "turn_index"} <= cols
    db._initialized.clear()
    with db.connect():  # idempotent
        pass


def test_draft_runs_are_excluded_from_lists_and_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    agent, _ = db.create_agent("a", "A")
    v = db.create_version(agent["id"], system_prompt="p", model="fake:echo")
    draft = db.create_session(agent["id"], v["id"], version_id=None, draft_config={"system_prompt": "new"})
    saved = db.create_session(agent["id"], v["id"], version_id=v["id"])
    r1 = db.create_run(agent["id"], v["id"], "playground", "draft turn", session_id=draft["id"], turn_index=0)
    r2 = db.create_run(agent["id"], v["id"], "playground", "saved turn", session_id=saved["id"], turn_index=0)
    r3 = db.create_run(agent["id"], v["id"], "ui", "plain")
    assert {r["id"] for r in db.list_runs(agent["id"])} == {r2["id"], r3["id"]}
    for r in (r1, r2):
        db.create_feedback(r["id"], -1, "bad")
    assert [f["run_id"] for f in db.negative_feedback_since(agent["id"], 0)] == [r2["id"]]
    assert db.is_draft_run(r1) and not db.is_draft_run(r2) and not db.is_draft_run(r3)
    assert [r["id"] for r in db.session_runs(draft["id"])] == [r1["id"]]
    assert db.list_sessions(agent["id"])[0]["turn_count"] == 1


async def test_session_scenario_replays_per_turn():
    p = ScenarioProvider("session")
    first = await p.complete("", [Message(role="user", text="make")], [])
    assert first.tool_calls[0].name == "write_file" and first.tool_calls[0].id == "call_0"
    hist = [Message(role="user", text="make"), Message(role="assistant", text="Created hello.py."),
            Message(role="user", text="show")]
    second = await p.complete("", hist, [])
    assert second.tool_calls[0].args == {"cmd": "cat hello.py"} and second.tool_calls[0].id == "call_t1_0"
    hist += [Message(role="assistant", tool_calls=second.tool_calls),
             Message(role="tool", tool_call_id="call_t1_0",
                     text="exit_code: 0\n--- stdout ---\nprint('hi')\n--- stderr ---\n")]
    final = await p.complete("", hist, [])
    assert final.text == "Turn 2: hello.py contains `print('hi')` (history had 1 earlier turn(s))."
