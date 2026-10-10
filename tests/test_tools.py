"""Unit tests for tool sources: HTTP tools (schema, templating, secrets, SSRF), MCP mapping and connections,
agent call depth and cycle checks, the registry seam in the loop, and the fake:tools scenario. (FR15)

MCP connection tests start the tiny fixture server in tests/fixtures/mcp_server.py on this machine (stdio and
Streamable HTTP on 127.0.0.1); nothing leaves localhost.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from forge import db
from forge.executors import LocalExecutor
from forge.loop import run_agent
from forge.providers import FakeProvider, ScenarioProvider, tools_hint
from forge.tools import ToolRegistry, build_registry, validate_tool_config
from forge.tools import http as th
from forge.tools.agents import MAX_DEPTH, check_call
from forge.tools.http import HttpSource, run_http_tool
from forge.tools.mcp import McpConnection, McpSource, map_result, tool_name
from forge.types import AgentVersion, Event, HttpTool, McpServer, ModelResponse, ToolCall, ToolSpec

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_server.py"
PUBLIC_IP = "93.184.216.34"
SECRET = "sk-test-5e3c7a9b1d"


def far() -> float:
    return time.monotonic() + 60


def tool(**kw) -> HttpTool:
    base = {"name": "get_item", "description": "Get an item",
            "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "q": {"type": "string"},
                                                            "n": {"type": "integer"}}, "required": ["id"]},
            "url": "https://api.example.com/items/{id}", "method": "GET"}
    return HttpTool(**{**base, **kw})


@pytest.fixture
def public_dns(monkeypatch):
    """Every hostname resolves to a public IP unless listed in the returned mapping."""
    table: dict[str, list[str]] = {}

    async def fake_resolve(host: str, port: int) -> list[str]:
        if host in table:
            return table[host]
        try:
            import ipaddress

            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            return [PUBLIC_IP]

    monkeypatch.setattr(th, "resolve", fake_resolve)
    monkeypatch.delenv("FORGE_ALLOW_PRIVATE_HTTP", raising=False)
    return table


# ---------------------------------------------------------------- schema and templating


def test_schema_validation_rejects_bad_schemas_and_bad_args():
    """FR15: parameters must be an object schema; arguments are validated before any request."""
    assert th.check_schema({"type": "object", "properties": {"a": {"type": "string"}}}) is None
    assert "object" in th.check_schema({"type": "string"})
    assert "invalid JSON schema" in th.check_schema({"type": "object", "properties": {"a": {"type": 5}}})
    schema = tool().parameters
    assert th.validate_arguments(schema, {"id": "x"}, "t") is None
    assert "'id' is a required property" in th.validate_arguments(schema, {}, "t")
    assert "not of type 'integer'" in th.validate_arguments(schema, {"id": "x", "n": "five"}, "t")


def test_url_template_encodes_values_and_splits_query_and_body():
    url, used = th.render_url("https://h/items/{id}/sub/{name}", {"id": "a b/c", "name": True, "x": 1})
    assert url == "https://h/items/a%20b%2Fc/sub/true" and used == {"id", "name"}
    with pytest.raises(th.ToolError, match="missing value"):
        th.render_url("https://h/{id}", {})
    get = tool()
    assert th.split_arguments(get, {"id": "1", "q": "x", "n": None}, {"id"}) == ({"q": "x"}, None)
    post = tool(method="POST", query_params=["q"])
    assert th.split_arguments(post, {"id": "1", "q": "x", "n": 2}, {"id"}) == ({"q": "x"}, {"n": 2})


def test_secret_substitution_and_missing_secret():
    assert th.substitute_secrets("Bearer {{API_KEY}}", {"API_KEY": SECRET}) == f"Bearer {SECRET}"
    assert th.substitute_secrets("{{ API_KEY }}-x", {"API_KEY": "k"}) == "k-x"
    with pytest.raises(th.ToolError, match="secret OTHER is not set"):
        th.substitute_secrets("{{OTHER}}", {"API_KEY": "k"})
    with pytest.raises(th.ToolError, match="only be referenced in headers"):
        th.render_url("https://h/?k={{API_KEY}}", {})


def test_tool_config_validation():
    bad = [tool(name="bash"), tool(name="mcp__x"), tool(name="ok", url="https://h/{missing}"),
           tool(name="ok2", parameters={"type": "array"}), tool(name="ok3", url="ftp://h")]
    errors = validate_tool_config(bad, [McpServer(name="a__b"), McpServer(name="s", transport="stdio")],
                                  ["me", "x", "x"], own_slug="me")
    text = " | ".join(errors)
    for needle in ("'bash' is reserved", "'mcp__x' is reserved", "{missing} is not a parameter",
                   '"type": "object"', "http:// or https://", "'a__b'", "a command is required",
                   "cannot call itself", "must not repeat"):
        assert needle in text, (needle, text)
    assert validate_tool_config([tool()], [McpServer(name="docs", url="https://m/mcp")], ["other"], "me") == []


# ---------------------------------------------------------------- SSRF


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0",
                                "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "100.64.0.1", "224.0.0.1"])
def test_private_addresses_are_blocked(ip):
    assert th.is_blocked_ip(ip)


def test_public_addresses_are_allowed():
    assert not th.is_blocked_ip(PUBLIC_IP) and not th.is_blocked_ip("2606:4700:4700::1111")


async def test_localhost_hostname_is_blocked(monkeypatch):
    """`localhost` really resolves (no network needed) to loopback, so it is refused."""
    monkeypatch.delenv("FORGE_ALLOW_PRIVATE_HTTP", raising=False)
    out = await run_http_tool(tool(url="http://localhost:9/items/{id}"), {"id": "1"}, secrets={}, network=True)
    assert out.is_error and "blocked request to localhost" in out.output


async def test_hostname_resolving_to_loopback_is_blocked(public_dns):
    public_dns["sneaky.example"] = ["127.0.0.1"]
    calls = []
    transport = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, text="should not happen"))
    out = await run_http_tool(tool(url="https://sneaky.example/items/{id}"), {"id": "1"}, secrets={}, network=True,
                              transport=transport)
    assert out.is_error and "127.0.0.1" in out.output and not calls


async def test_any_private_answer_blocks_even_with_public_ones(public_dns):
    public_dns["mixed.example"] = [PUBLIC_IP, "10.0.0.7"]
    out = await run_http_tool(tool(url="https://mixed.example/items/{id}"), {"id": "1"}, secrets={}, network=True)
    assert out.is_error and "10.0.0.7" in out.output


async def test_redirect_to_private_ip_is_blocked(public_dns):
    public_dns["internal.example"] = ["169.254.169.254"]
    seen: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(str(r.url))
        if r.headers["host"] == "api.example.com":
            return httpx.Response(302, headers={"location": "http://10.0.0.5/latest/meta-data"})
        return httpx.Response(200, text="secret metadata")

    out = await run_http_tool(tool(), {"id": "1"}, secrets={}, network=True, transport=httpx.MockTransport(handler))
    assert out.is_error and "10.0.0.5" in out.output and len(seen) == 1

    def handler2(r: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "https://internal.example/x"})

    out = await run_http_tool(tool(), {"id": "1"}, secrets={}, network=True, transport=httpx.MockTransport(handler2))
    assert out.is_error and "169.254.169.254" in out.output


async def test_requests_are_pinned_to_the_checked_ip_and_redirects_are_capped(public_dns):
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return httpx.Response(302, headers={"location": f"/items/{len(seen)}"})

    out = await run_http_tool(tool(), {"id": "1"}, secrets={}, network=True, transport=httpx.MockTransport(handler))
    assert out.is_error and "too many redirects" in out.output
    assert len(seen) == th.MAX_REDIRECTS + 1
    assert seen[0].url.host == PUBLIC_IP and seen[0].headers["host"] == "api.example.com"
    assert seen[0].extensions.get("sni_hostname") == "api.example.com"


async def test_cross_origin_redirect_drops_configured_headers(public_dns):
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        if r.headers["host"] == "api.example.com":
            return httpx.Response(307, headers={"location": "https://other.example/landing"})
        return httpx.Response(200, text="ok")

    t = tool(headers={"Authorization": "Bearer {{API_KEY}}"})
    out = await run_http_tool(t, {"id": "1"}, secrets={"API_KEY": SECRET}, network=True,
                              transport=httpx.MockTransport(handler))
    assert not out.is_error
    assert seen[0].headers["authorization"] == f"Bearer {SECRET}"
    assert "authorization" not in seen[1].headers


async def test_network_policy_and_private_override(monkeypatch):
    out = await run_http_tool(tool(), {"id": "1"}, secrets={}, network=False)
    assert out.is_error and "network access is disabled" in out.output
    monkeypatch.setenv("FORGE_ALLOW_PRIVATE_HTTP", "1")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"host": r.url.host}))
    out = await run_http_tool(tool(url="http://127.0.0.1:1/items/{id}"), {"id": "1"}, secrets={}, network=True,
                              transport=transport)
    assert not out.is_error and '"host": "127.0.0.1"' in out.output.replace('":"', '": "')


# ---------------------------------------------------------------- responses


async def test_success_non_2xx_truncation_and_body_mapping(public_dns):
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/missing"):
            return httpx.Response(404, text="no such item " + "x" * 5000)
        if r.url.path.endswith("/big"):
            return httpx.Response(200, text="y" * 50_000)
        return httpx.Response(201, json={"method": r.method, "query": dict(r.url.params),
                                         "body": json.loads(r.content or b"null")})

    mt = httpx.MockTransport(handler)
    out = await run_http_tool(tool(method="POST", query_params=["q"]), {"id": "1", "q": "s", "n": 3}, secrets={},
                              network=True, transport=mt)
    assert not out.is_error and out.output.startswith("HTTP 201 Created")
    body = json.loads(out.output.split("\n\n", 1)[1])
    assert body == {"method": "POST", "query": {"q": "s"}, "body": {"n": 3}}
    out = await run_http_tool(tool(), {"id": "missing"}, secrets={}, network=True, transport=mt)
    assert out.is_error and out.output.startswith("HTTP 404") and "no such item" in out.output
    assert out.extra["http_status"] == 404 and len(out.output) < 2_200
    out = await run_http_tool(tool(max_response_chars=1000), {"id": "big"}, secrets={}, network=True, transport=mt)
    assert "chars truncated" in out.output and len(out.output) < 1_200
    out = await run_http_tool(tool(), {"n": 1}, secrets={}, network=True, transport=mt)
    assert out.is_error and "required property" in out.output


async def test_timeout_is_an_error(public_dns):
    async def slow(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=r)

    out = await run_http_tool(tool(timeout=1), {"id": "1"}, secrets={}, network=True,
                              transport=httpx.MockTransport(slow))
    assert out.is_error and "timed out" in out.output


# ---------------------------------------------------------------- the loop seam and redaction


async def collect_run(version, provider, registry, executor=None, secrets=None):
    evs: list[Event] = []

    async def emit(e: Event) -> None:
        evs.append(e)

    ex = executor or LocalExecutor()
    if executor is None:
        await ex.start({}, {})
    try:
        result = await run_agent(version, "go", run_id="r_unit", provider=provider, executor=ex, emit=emit,
                                 secrets=secrets, extra_tools=registry)
    finally:
        if executor is None:
            await ex.stop()
    return result, evs


def version(**kw) -> AgentVersion:
    return AgentVersion(id="v_t", agent_id="ag_t", n=1, system_prompt="t", model="fake:x", **kw)


async def test_http_tool_through_loop_is_traced_and_secret_redacted(public_dns):
    """FR15, NFR6: the tool is offered with its schema, traced, and the header secret never reaches events."""
    t = tool(headers={"X-Key": "{{API_KEY}}"})

    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"you sent {r.headers['x-key']}")

    src = HttpSource([t], secrets={"API_KEY": SECRET}, network=True, transport=httpx.MockTransport(handler))
    provider = FakeProvider([
        ModelResponse(tool_calls=[ToolCall(id="c1", name="get_item", args={"id": "7"})]),
        ModelResponse(text="done"),
    ])
    result, evs = await collect_run(version(http_tools=[t], network=True), provider, ToolRegistry([src]),
                                    secrets={"API_KEY": SECRET})
    assert result.status == "succeeded"
    offered = provider.calls[0]["tools"]
    assert [s.name for s in offered][-1] == "get_item" and offered[-1].parameters == t.parameters
    tr = next(e for e in evs if e.type == "tool_result").payload
    assert tr["source"] == "http" and tr["http_status"] == 200 and not tr["is_error"]
    assert "you sent [REDACTED]" in tr["output"]
    assert SECRET not in json.dumps([e.model_dump() for e in evs])


class Exploding:
    kind = "http"

    async def open(self, notify):
        return None

    def specs(self):
        return [ToolSpec(name="bash", description="dup", parameters={"type": "object"}),
                ToolSpec(name="explode", description="x", parameters={"type": "object"})]

    async def call(self, name, args, deadline):
        raise RuntimeError("kaboom")

    async def close(self):
        raise RuntimeError("close fails too")


async def test_registry_failures_never_crash_the_run():
    provider = FakeProvider([
        ModelResponse(tool_calls=[ToolCall(id="c1", name="explode", args={})]),
        ModelResponse(text="survived"),
    ])
    result, evs = await collect_run(version(), provider, ToolRegistry([Exploding()]))
    assert result.status == "succeeded" and result.output == "survived"
    assert [s.name for s in provider.calls[0]["tools"]].count("bash") == 1  # builtin wins over a duplicate
    assert any(e.type == "error" and "already taken" in e.payload["message"] for e in evs)
    tr = next(e for e in evs if e.type == "tool_result").payload
    assert tr["is_error"] and "kaboom" in tr["output"]


def test_build_registry_is_none_without_extra_tools():
    assert build_registry(version()) is None
    assert build_registry(version(callable_agents=["x"])) is None  # no run manager: agent tools not offered


# ---------------------------------------------------------------- MCP mapping


def test_mcp_result_mapping():
    text, err = map_result({"content": [
        {"type": "text", "text": "hello"}, {"type": "image", "data": "AAAA", "mimeType": "image/png"},
        {"type": "resource", "resource": {"uri": "file:///a.txt", "text": "file body"}},
        {"type": "resource", "resource": {"uri": "file:///b.bin", "blob": "AAAA"}},
        {"type": "resource_link", "uri": "file:///c", "name": "c"}, {"type": "audio", "data": "x"},
    ], "isError": False})
    assert text.split("\n") == ["hello", "[image]", "file body", "[resource file:///b.bin]",
                                "[resource link: file:///c]", "[audio]"]
    assert not err
    assert map_result({"content": [], "structuredContent": {"a": 1}}) == ('{\n  "a": 1\n}', False)
    assert map_result({"content": [], "isError": True}) == ("(the tool reported an error)", True)


def test_mcp_tool_names_are_namespaced_and_sanitized():
    assert tool_name("docs", "search") == "mcp__docs__search"
    assert tool_name("docs", "get.page/v2") == "mcp__docs__get_page_v2"
    assert len(tool_name("s", "x" * 100)) == 64


# ---------------------------------------------------------------- MCP connections (local fixture server)


def stdio_server(**kw) -> McpServer:
    return McpServer(name="local", transport="stdio", command=sys.executable, args=[str(FIXTURE), "stdio"], **kw)


async def test_mcp_stdio_on_host_lists_calls_and_maps_results(tmp_path):
    """Local executor in auto mode: the server runs on the host in the workspace, env secrets substituted."""
    ex = LocalExecutor(root=str(tmp_path), mode="auto")
    await ex.start({}, {})
    src = McpSource([stdio_server(env={"TEST_TOKEN": "{{API_KEY}}"}, timeout=3)], secrets={"API_KEY": SECRET},
                    executor=ex, network=False)
    errors: list[dict] = []

    async def notify(t, p):
        errors.append(p)

    await src.open(notify)
    try:
        assert not errors
        names = {s.name for s in src.specs()}
        assert {"mcp__local__add", "mcp__local__picture", "mcp__local__whoami"} <= names
        add = next(s for s in src.specs() if s.name == "mcp__local__add")
        assert add.parameters["required"] == ["a", "b"]
        assert (await src.call("mcp__local__add", {"a": 2, "b": 3}, far())).output == "5"
        pic = await src.call("mcp__local__picture", {}, far())
        assert pic.output == "[image]\na 1px picture" and not pic.is_error
        assert (await src.call("mcp__local__whoami", {}, far())).output == f"token={SECRET}"
        assert (await src.call("mcp__local__where", {}, far())).output == f"cwd={tmp_path.resolve()}"
        boom = await src.call("mcp__local__boom", {}, far())
        assert boom.is_error
        slow = await src.call("mcp__local__sleep", {"seconds": 10}, far())
        assert slow.is_error and "timed out" in slow.output
        assert (await src.call("mcp__local__add", {"a": 1, "b": 1}, far())).output == "2"  # still usable
    finally:
        await src.close()
        await ex.stop()


async def test_mcp_stdio_minimal_fallback_server(tmp_path):
    """The SDK-free fallback (used inside the sandbox image) speaks enough MCP for the SDK client."""
    ex = LocalExecutor(root=str(tmp_path), mode="auto")
    await ex.start({}, {})
    conn = McpConnection(stdio_server(env={"FORCE_MINIMAL": "1"}), secrets={}, executor=ex, network=False)
    try:
        tools = await conn.start()
        assert "add" in {t.name for t in tools}
        assert (await conn.call("picture", {}, 5)).output == "[image]\na 1px picture"
    finally:
        await conn.close()
        await ex.stop()


async def test_mcp_ask_mode_declined_and_failing_server_emit_errors_and_run_continues(tmp_path):
    ex = LocalExecutor(root=str(tmp_path), mode="ask", confirm=lambda d: "declined" not in d)
    await ex.start({}, {})
    servers = [McpServer(name="declined", transport="stdio", command=sys.executable, args=[str(FIXTURE)]),
               McpServer(name="broken", transport="stdio", command=sys.executable,
                         args=["-c", "import sys; sys.stderr.write('cannot start'); sys.exit(3)"])]
    provider = FakeProvider([ModelResponse(text="carried on")])
    registry = ToolRegistry([McpSource(servers, secrets={}, executor=ex, network=False)])
    result, evs = await collect_run(version(mcp_servers=servers), provider, registry, executor=ex)
    await ex.stop()
    assert result.status == "succeeded" and result.output == "carried on"
    msgs = [e.payload["message"] for e in evs if e.type == "error"]
    assert any("'declined' failed to start" in m and "declined" in m for m in msgs), msgs
    assert any("'broken' failed to start" in m for m in msgs), msgs
    assert [e.type for e in evs][-1] == "run_finished"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mcp_http_url():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(FIXTURE), "http", str(port)], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_mcp_http_headers_policy_and_private_check(mcp_http_url, monkeypatch):
    server = McpServer(name="remote", url=mcp_http_url, headers={"X-Test-Token": "{{API_KEY}}"})
    monkeypatch.delenv("FORGE_ALLOW_PRIVATE_HTTP", raising=False)
    conn = McpConnection(server, secrets={"API_KEY": SECRET}, executor=None, network=True)
    with pytest.raises(Exception, match="blocked request to 127.0.0.1"):
        await conn.start()
    conn = McpConnection(server, secrets={"API_KEY": SECRET}, executor=None, network=False)
    with pytest.raises(Exception, match="network access is disabled"):
        await conn.start()

    monkeypatch.setenv("FORGE_ALLOW_PRIVATE_HTTP", "1")
    provider = FakeProvider([
        ModelResponse(tool_calls=[ToolCall(id="c1", name="mcp__remote__whoami", args={})]),
        ModelResponse(tool_calls=[ToolCall(id="c2", name="mcp__remote__echo", args={"text": "hi"})]),
        ModelResponse(text="done"),
    ])
    registry = ToolRegistry([McpSource([server], secrets={"API_KEY": SECRET}, executor=None, network=True)])
    result, evs = await collect_run(version(mcp_servers=[server], network=True), provider, registry,
                                    secrets={"API_KEY": SECRET})
    assert result.status == "succeeded"
    outs = [e.payload for e in evs if e.type == "tool_result"]
    assert outs[0]["output"] == "token=[REDACTED]" and outs[0]["source"] == "mcp"
    assert outs[1]["output"] == "hi" and outs[1]["mcp_server"] == "remote"
    assert SECRET not in json.dumps([e.model_dump() for e in evs])


# ---------------------------------------------------------------- agents calling agents


def test_depth_limit_and_cycle_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    agents = {}
    for slug in ("a", "b", "c", "d", "e"):
        ag, _ = db.create_agent(slug, slug)
        v = db.create_version(ag["id"], system_prompt="x", model="fake:echo")
        agents[slug] = (ag, v)
    ra = db.create_run(agents["a"][0]["id"], agents["a"][1]["id"], "api", "top")
    rb = db.create_run(agents["b"][0]["id"], agents["b"][1]["id"], "agent", "x", parent_run_id=ra["id"])
    rc = db.create_run(agents["c"][0]["id"], agents["c"][1]["id"], "agent", "x", parent_run_id=rb["id"])
    rd = db.create_run(agents["d"][0]["id"], agents["d"][1]["id"], "agent", "x", parent_run_id=rc["id"])
    assert [r["id"] for r in db.run_ancestry(rd["id"])] == [rd["id"], rc["id"], rb["id"], ra["id"]]

    assert check_call(ra["id"], agents["b"][0]["id"], "b") is None
    assert "cycle detected" in check_call(ra["id"], agents["a"][0]["id"], "a")  # itself
    err = check_call(rc["id"], agents["a"][0]["id"], "a")  # an ancestor
    assert "cycle detected" in err and "a > b > c > a" in err
    assert check_call(rc["id"], agents["e"][0]["id"], "e") is None  # depth 3 is the deepest allowed
    assert "depth limit of 3" in check_call(rd["id"], agents["e"][0]["id"], "e")
    assert MAX_DEPTH == 3

    db.add_child_usage(ra["id"], 30, 15, 0.5)
    r = db.get_run(ra["id"])
    assert (r["child_tokens_in"], r["child_tokens_out"], r["child_cost_usd"], r["tokens_in"]) == (30, 15, 0.5, 0)


# ---------------------------------------------------------------- fake:tools scenario


def test_tools_hint_parsing():
    assert tools_hint('please {"tool": "get_item", "args": {"id": "1"}} thanks') == [("get_item", {"id": "1"})]
    assert tools_hint('{"calls": [{"tool": "a"}, {"tool": "b", "args": {"x": 1}}]}') == [("a", {}), ("b", {"x": 1})]
    assert tools_hint("no hint here") == [("", {})]


async def test_fake_tools_scenario_calls_non_builtin_tools_then_reports():
    p = ScenarioProvider("tools")
    from forge.loop import TOOL_SPECS
    from forge.types import Message

    specs = [*TOOL_SPECS.values(), ToolSpec(name="mcp__s__add", description="", parameters={}),
             ToolSpec(name="get_item", description="", parameters={})]
    msgs = [Message(role="user", text='{"calls": [{"tool": "get_item", "args": {"id": "1"}}, {"tool": "mcp__s"}]}')]
    r = await p.complete("", msgs, specs)
    assert r.tool_calls[0].name == "get_item" and r.tool_calls[0].args == {"id": "1"}
    msgs += [Message(role="assistant", tool_calls=r.tool_calls), Message(role="tool", tool_call_id="call_0", text="A")]
    r = await p.complete("", msgs, specs)
    assert r.tool_calls[0].name == "mcp__s__add"
    msgs += [Message(role="assistant", tool_calls=r.tool_calls), Message(role="tool", tool_call_id="call_1", text="B")]
    r = await p.complete("", msgs, specs)
    assert not r.tool_calls and r.text == "Tool results:\nA\n---\nB"
    r = await p.complete("", [Message(role="user", text="hi")], specs)
    assert r.tool_calls[0].name == "mcp__s__add"  # no hint: the first non-builtin tool


# ---------------------------------------------------------------- run webhooks use the same SSRF-safe client


async def test_webhooks_refuse_private_targets_and_redirects_and_are_signed(public_dns, tmp_path, monkeypatch):
    import hashlib
    import hmac

    from forge.server.runs import post_webhook

    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("FORGE_ALLOW_PRIVATE_HTTP", raising=False)
    agent, _ = db.create_agent("hooked", "Hooked")
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        if r.headers["host"] == "redirector.example":
            return httpx.Response(307, headers={"location": "http://10.1.2.3/hook"})
        return httpx.Response(200)

    mock = httpx.MockTransport(handler)
    assert not await post_webhook("http://127.0.0.1:9/hook", {"a": 1}, retries=0, agent_id=agent["id"], transport=mock)
    public_dns["metadata.example"] = ["169.254.169.254"]
    assert not await post_webhook("http://metadata.example/x", {"a": 1}, retries=0, transport=mock)
    assert seen == []  # nothing was sent to a private address
    assert not await post_webhook("https://redirector.example/hook", {"a": 1}, retries=0, transport=mock)
    assert [r.headers["host"] for r in seen] == ["redirector.example"]  # the private hop was never requested

    seen.clear()
    body = {"id": "r_1", "status": "succeeded"}
    assert await post_webhook("https://hooks.example/forge", body, retries=0, agent_id=agent["id"], transport=mock,
                              event="run.awaiting_approval")
    [req] = seen
    secret = db.webhook_secret(agent["id"])
    assert secret.startswith("whsec_") and db.get_agent(agent["id"])["webhook_secret"] == secret
    want = "sha256=" + hmac.new(secret.encode(), req.content, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(req.headers["x-forge-signature-256"], want)
    assert json.loads(req.content) == body and req.headers["x-forge-event"] == "run.awaiting_approval"
    assert len(req.headers["x-forge-delivery"]) == 32
    assert db.webhook_secret(agent["id"], rotate=True) != secret
