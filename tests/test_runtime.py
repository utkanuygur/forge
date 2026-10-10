from __future__ import annotations

import asyncio
import shutil
import subprocess
import tarfile

import pytest

from forge.executors import DockerExecutor, LocalExecutor, make_executor, safe_relpath
from forge.loop import TOOL_SPECS, build_system_prompt, run_agent, truncate
from forge.providers import (
    AnthropicProvider,
    FakeProvider,
    OpenAICompatProvider,
    make_provider,
    parse_args,
    strip_think,
)
from forge.types import AgentVersion, Event, Message, ModelResponse, ToolCall


def version(**kw) -> AgentVersion:
    return AgentVersion(
        id="v_test", agent_id="ag_test", n=1, system_prompt="You are a test agent.", model="fake:x", **kw
    )


def call(i: int, name: str, **args) -> ToolCall:
    return ToolCall(id=f"c{i}", name=name, args=args)


def reply(*calls: ToolCall, text: str = "") -> ModelResponse:
    return ModelResponse(text=text, tool_calls=list(calls), tokens_in=10, tokens_out=5)


async def run(responses, executor, **kw):
    events: list[Event] = []

    async def emit(e: Event) -> None:
        events.append(e)

    provider = FakeProvider(responses)
    result = await run_agent(
        kw.pop("version", version()), "do it", run_id="r_test", provider=provider, executor=executor, emit=emit, **kw
    )
    return result, events, provider


def results(events: list[Event]) -> list[dict]:
    return [e.payload for e in events if e.type == "tool_result"]


@pytest.fixture
async def local(tmp_path):
    ex = LocalExecutor(root=str(tmp_path / "ws"))
    await ex.start({"seed.txt": b"seed\n"}, {})
    yield ex
    await ex.stop()


# ---------------------------------------------------------------- loop


async def test_loop_write_edit_run_finish(local):
    result, events, provider = await run(
        [
            reply(call(1, "write_file", path="pkg/hello.py", content="print('hello world')\n")),
            reply(call(2, "edit_file", path="pkg/hello.py", old="hello world", new="hello forge")),
            reply(call(3, "bash", cmd="python3 pkg/hello.py"), call(4, "read_file", path="pkg/hello.py")),
            reply(text="Done: wrote and ran hello.py"),
        ],
        local,
    )
    assert result.status == "succeeded" and result.output == "Done: wrote and ran hello.py"
    assert result.turns == 4 and result.tokens_in == 40 and result.tokens_out == 20
    assert [e.seq for e in events] == list(range(len(events)))
    assert events[0].type == "run_started" and events[-1].type == "run_finished"
    assert events[-1].payload == {
        "status": "succeeded",
        "output": result.output,
        "turns": 4,
        "tokens_in": 40,
        "tokens_out": 20,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    res = results(events)
    assert not any(r["is_error"] for r in res)
    assert res[2]["output"].startswith("exit_code: 0\n--- stdout ---\nhello forge")
    assert res[3]["output"] == "1\tprint('hello forge')"
    # consecutive tool results are sent back as tool messages after the assistant turn
    last = provider.calls[-1]["messages"]
    assert [m.role for m in last[-3:]] == ["assistant", "tool", "tool"]
    assert "## Environment" in provider.calls[0]["system"]


async def test_edit_file_errors(local):
    await local.write("dup.txt", "x\nx\n")
    _, events, _ = await run(
        [
            reply(call(1, "edit_file", path="dup.txt", old="missing", new="y")),
            reply(call(2, "edit_file", path="dup.txt", old="x", new="y")),
            reply(call(3, "nope", a=1), call(4, "write_file", path="a.txt")),
            reply(call(5, "read_file", path="../escape.txt")),
            reply(text="ok"),
        ],
        local,
    )
    r = results(events)
    assert all(x["is_error"] for x in r)
    assert "not found" in r[0]["output"]
    assert "found 2 times" in r[1]["output"] and "add more context" in r[1]["output"]
    assert "unknown tool" in r[2]["output"]
    assert "missing required" in r[3]["output"]
    assert "escapes" in r[4]["output"]
    assert await local.read("dup.txt") == "x\nx\n"


async def test_truncation(local):
    s = "a" * 4000 + "b" * 5000 + "c" * 6000
    t = truncate(s)
    assert t.startswith("a" * 4000) and t.endswith("c" * 6000) and "[... 5000 chars truncated ...]" in t
    assert truncate("short") == "short"
    _, events, _ = await run([reply(call(1, "bash", cmd="python3 -c \"print('z'*50000)\"")), reply(text="ok")], local)
    out = results(events)[0]["output"]
    assert "chars truncated" in out and len(out) < 10_100


async def test_secret_redaction(local):
    secret = "sk-supersecret-123"
    local.env = {"API_TOKEN": secret}
    result, events, _ = await run(
        [
            reply(call(1, "bash", cmd="echo token=$API_TOKEN"), text=f"I will print {secret}"),
            reply(text=f"it was {secret}"),
        ],
        local,
        secrets={"API_TOKEN": secret, "EMPTY": ""},
    )
    dumped = "".join(e.model_dump_json() for e in events)
    assert secret not in dumped
    assert "token=[REDACTED]" in results(events)[0]["output"]
    assert secret not in result.output


async def test_turn_limit_timeout(local):
    result, events, _ = await run([reply(call(i, "bash", cmd="true")) for i in range(5)], local, max_turns=3)
    assert result.status == "timeout" and result.turns == 3
    assert events[-1].type == "run_finished" and events[-1].payload["status"] == "timeout"


async def test_provider_failure(local):
    result, events, _ = await run([RuntimeError("boom")], local)  # type: ignore[list-item]
    assert result.status == "failed" and "boom" in (result.error or "")
    assert [e.type for e in events] == ["run_started", "error", "run_finished"]


async def test_bash_timeout(local):
    _, events, _ = await run([reply(call(1, "bash", cmd="sleep 10", timeout=1)), reply(text="ok")], local)
    out = results(events)[0]["output"]
    assert out.startswith("exit_code: 124") and "timed out" in out


def test_system_prompt_and_specs():
    p = build_system_prompt(version(lessons=["Run make check", "No prints"], network=True))
    assert "## Lessons learned\n- Run make check\n- No prints" in p and "Network access is enabled" in p
    assert "disabled" in build_system_prompt(version())
    assert set(TOOL_SPECS) == {"bash", "read_file", "write_file", "edit_file"}


# ---------------------------------------------------------------- providers


def test_provider_helpers():
    assert strip_think("<think>hmm\nlong</think>\nAnswer") == "Answer"
    assert parse_args('{"a": 1}') == {"a": 1}
    assert parse_args("{bad") == {"_raw": "{bad"}
    assert isinstance(make_provider("anthropic:claude-sonnet-5-5"), AnthropicProvider)
    p = make_provider("openai_compat:http://localhost:11434/v1|qwen3:8b")
    assert isinstance(p, OpenAICompatProvider) and p.model == "qwen3:8b"
    with pytest.raises(ValueError):
        make_provider("bogus")


def test_anthropic_message_mapping():
    p = AnthropicProvider("m", client=object())
    msgs = p._messages(
        [
            Message(role="user", text="hi"),
            Message(
                role="assistant",
                text="ok",
                tool_calls=[
                    ToolCall(id="a", name="bash", args={"cmd": "ls"}),
                    ToolCall(id="b", name="bash", args={"cmd": "pwd"}),
                ],
            ),
            Message(role="tool", tool_call_id="a", text="x"),
            Message(role="tool", tool_call_id="b", text="y", is_error=True),
        ]
    )
    assert len(msgs) == 3
    assert [b["type"] for b in msgs[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert msgs[2]["role"] == "user" and [b["tool_use_id"] for b in msgs[2]["content"]] == ["a", "b"]
    assert msgs[2]["content"][1]["is_error"] is True


# ---------------------------------------------------------------- executors


async def test_local_path_escape_and_diff(tmp_path):
    ex = make_executor("local", root=str(tmp_path / "ws"))
    await ex.start({"a.txt": b"one\n"}, {})
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(ValueError):
            await ex.read(bad)
        with pytest.raises(ValueError):
            await ex.write(bad, "x")
    (tmp_path / "outside.txt").write_text("secret")
    (tmp_path / "ws" / "link").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ValueError):
        await ex.read("link")
    await ex.write("a.txt", "two\n")
    await ex.write("new/b.txt", "b\n")
    await ex.export_workspace(str(tmp_path / "out" / "ws.tar.gz"), str(tmp_path / "out" / "diff.patch"))
    diff = (tmp_path / "out" / "diff.patch").read_text()
    assert "+two" in diff and "new/b.txt" in diff
    await ex.stop()
    assert (tmp_path / "ws" / "a.txt").exists()  # caller-provided root survives stop()


async def test_local_existing_repo_and_ask_mode(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "f.txt").write_text("v1\n")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=repo, check=True)
    asked: list[str] = []
    ex = LocalExecutor(root=str(repo), mode="ask", confirm=lambda d: asked.append(d) or len(asked) > 1)
    await ex.start({}, {})
    r = await ex.exec("echo hi")
    assert r.exit_code != 0 and "declined" in r.stderr  # first prompt denied
    await ex.write("untracked.txt", "u\n")  # second prompt allowed
    await ex.export_workspace(str(tmp_path / "a.tgz"), str(tmp_path / "d.patch"))
    assert "untracked.txt" in (tmp_path / "d.patch").read_text()
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo, capture_output=True, text=True).stdout
    assert staged == ""  # user's index untouched
    await ex.stop()
    assert (repo / "f.txt").exists()


def test_safe_relpath():
    assert safe_relpath("a/./b/../c") == "a/c"
    assert safe_relpath("") == "."
    for bad in ("/abs", "..", "../x", "a/../../x"):
        with pytest.raises(ValueError):
            safe_relpath(bad)


async def test_workspace_absolute_paths_are_relative(tmp_path):
    """Models (Claude in particular) pass the sandbox's absolute root: `/workspace/x` means `x`."""
    assert safe_relpath("/workspace/src/a.py") == "src/a.py"
    assert safe_relpath("/workspace") == safe_relpath("/workspace/") == "."
    for bad in ("/workspace/../etc/passwd", "/workspacefoo/a", "/workspace/../../x", "/etc/workspace/a"):
        with pytest.raises(ValueError):
            safe_relpath(bad)
    ex = LocalExecutor(root=tmp_path / "ws")
    await ex.start({}, {})
    await ex.write("/workspace/notes/a.txt", "hi")
    assert await ex.read("notes/a.txt") == "hi" == await ex.read("/workspace/notes/a.txt")
    await ex.stop()


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return (
            subprocess.run(["docker", "image", "inspect", "forge-sandbox"], capture_output=True, timeout=20).returncode
            == 0
        )
    except Exception:
        return False


@pytest.mark.skipif(not _docker_ok(), reason="docker or forge-sandbox image unavailable")
async def test_docker_executor(tmp_path):
    ex = DockerExecutor(network=False)
    try:
        await ex.start({"src/app.py": b"print('hi')\n"}, {"FOO": "bar"})
        r = await ex.exec("curl -sS -m 5 https://example.com", timeout=20)
        assert r.exit_code != 0  # no egress with --network none
        r = await ex.exec("python3 src/app.py && echo $FOO && whoami")
        assert r.stdout.split() == ["hi", "bar", "agent"]
        await ex.write("src/new.py", "x = 1\n")
        await ex.exec("echo more >> src/app.py")
        assert await ex.read("src/new.py") == "x = 1\n"
        assert "src/new.py" in await ex.list()
        with pytest.raises(ValueError):
            await ex.read("../etc/passwd")
        r = await ex.exec("sleep 30", timeout=1)
        assert r.timed_out and r.exit_code == 124
        await ex.export_workspace(str(tmp_path / "ws.tar.gz"), str(tmp_path / "diff.patch"))
        diff = (tmp_path / "diff.patch").read_text()
        assert "+more" in diff and "src/new.py" in diff
        with tarfile.open(tmp_path / "ws.tar.gz") as tf:
            assert "./src/new.py" in tf.getnames()
    finally:
        await ex.stop()
        await ex.stop()  # idempotent


@pytest.mark.skipif(not _docker_ok(), reason="docker or forge-sandbox image unavailable")
async def test_docker_loop_end_to_end():
    ex = DockerExecutor()
    try:
        await ex.start({}, {})
        result, events, _ = await run(
            [
                reply(call(1, "write_file", path="t.py", content="print(2+2)")),
                reply(call(2, "bash", cmd="python t.py")),
                reply(text="4"),
            ],
            ex,
        )
        assert result.status == "succeeded"
        assert "exit_code: 0\n--- stdout ---\n4" in results(events)[1]["output"]
    finally:
        await ex.stop()


async def test_wall_clock_timeout(local):
    class Slow(FakeProvider):
        async def complete(self, *a, **k):
            await asyncio.sleep(5)

    events: list[Event] = []

    async def emit(e: Event) -> None:
        events.append(e)

    result = await run_agent(version(), "x", run_id="r", provider=Slow([]), executor=local, emit=emit, max_seconds=1)
    assert result.status == "timeout" and events[-1].type == "run_finished"


def test_temperature_only_sent_to_models_that_accept_it():
    """Opus 4.7+ / Sonnet 5+ reject sampling parameters with a 400 (FR12)."""
    from forge.providers import accepts_temperature

    for m in ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-4-8", "claude-opus-4-7", "claude-fable-5-1"]:
        assert not accepts_temperature(m), m
    for m in ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-sonnet-4-5-20250929", "claude-3-7-sonnet-latest"]:
        assert accepts_temperature(m), m
