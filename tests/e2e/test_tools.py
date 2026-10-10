"""Full stack tool sources: HTTP tools, MCP servers (stdio inside Docker and Streamable HTTP) and agents calling
agents, driven by the `fake:tools` scenario against a real server and real sandboxes. (FR15)

Most tests here use their own server started with FORGE_ALLOW_PRIVATE_HTTP=1, because the HTTP endpoints they
call (a test HTTP server and the fixture MCP server) listen on 127.0.0.1. The shared default server proves that
the same request is blocked without that setting.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tests.conftest import docker_unavailable
from tests.e2e.conftest import Server, _free_port, start_server
from tests.e2e.helpers import assert_gapless, events, wait_run

pytestmark = pytest.mark.e2e

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_server.py"
SECRET = "sk-e2e-7b1f3c9d2a"


class Echo(BaseHTTPRequestHandler):
    def _reply(self) -> None:
        parts = urlsplit(self.path)
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"null") if n else None
        status = 404 if parts.path.endswith("/missing") else 200
        data = json.dumps({"method": self.command, "path": parts.path,
                           "query": {k: v[0] for k, v in parse_qs(parts.query).items()},
                           "body": body, "key": self.headers.get("x-key")}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _reply

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def echo_url() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


@pytest.fixture(scope="module")
def mcp_http_url() -> Iterator[str]:
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


@pytest.fixture(scope="module")
def tools_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    if why := docker_unavailable():
        pytest.skip(why)
    with start_server(tmp_path_factory.mktemp("forge-e2e-tools"), {"FORGE_ALLOW_PRIVATE_HTTP": "1"}) as srv:
        yield srv


@pytest.fixture(scope="module")
def tapi(tools_server: Server) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=tools_server.url + "/v1", timeout=120) as c:
        yield c


def create(client: httpx.Client, slug: str, **kw: Any) -> dict:
    body = {"slug": slug, "name": slug.split("-")[0], "system_prompt": "test", "model": "fake:tools", **kw}
    r = client.post("/agents", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def run(client: httpx.Client, slug: str, hint: Any) -> tuple[dict, list[dict]]:
    r = client.post(f"/agents/{slug}/runs", json={"input": json.dumps(hint), "wait": True})
    assert r.status_code == 200, r.text
    final = wait_run(client, r.json()["id"])
    evs = events(client, final["id"])
    assert_gapless(evs)
    return final, evs


def results(evs: list[dict]) -> list[dict]:
    return [e["payload"] for e in evs if e["type"] == "tool_result"]


def uid() -> str:
    return f"{time.time_ns() % 10**9:x}"


def get_item(url: str) -> dict:
    return {"name": "get_item", "description": "Fetch an item by id.", "method": "GET",
            "url": url + "/items/{id}", "headers": {"X-Key": "{{API_KEY}}"},
            "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "q": {"type": "string"}},
                           "required": ["id"]}}


# ---------------------------------------------------------------- HTTP tools


def test_http_tool_runs_from_server_with_secret_header_and_query(tapi, echo_url):
    """FR15, NFR6: URL template, query mapping, secret header substituted and redacted, non-2xx is an error."""
    slug = f"http-{uid()}"
    post = {"name": "create_item", "description": "Create an item.", "method": "POST", "url": echo_url + "/items",
            "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}
    create(tapi, slug, network=True, http_tools=[get_item(echo_url), post])
    assert tapi.put(f"/agents/{slug}/secrets", json={"API_KEY": SECRET}).status_code == 200
    final, evs = run(tapi, slug, {"calls": [
        {"tool": "get_item", "args": {"id": "a b", "q": "x"}},
        {"tool": "create_item", "args": {"title": "new"}},
        {"tool": "get_item", "args": {"id": "missing"}},
        {"tool": "get_item", "args": {"q": "no id"}},
    ]})
    assert final["status"] == "succeeded"
    calls = [e["payload"] for e in evs if e["type"] == "tool_call"]
    assert [c["name"] for c in calls] == ["get_item", "create_item", "get_item", "get_item"]
    r1, r2, r3, r4 = results(evs)
    got = json.loads(r1["output"].split("\n\n", 1)[1])
    assert got == {"method": "GET", "path": "/items/a%20b", "query": {"q": "x"}, "body": None, "key": "[REDACTED]"}
    assert r1["source"] == "http" and r1["http_status"] == 200 and not r1["is_error"]
    assert json.loads(r2["output"].split("\n\n", 1)[1])["body"] == {"title": "new"}
    assert r3["is_error"] and r3["output"].startswith("HTTP 404") and r3["http_status"] == 404
    assert r4["is_error"] and "'id' is a required property" in r4["output"]
    assert SECRET not in json.dumps(evs) and SECRET not in final["output"]


def test_http_tool_needs_network_and_test_endpoint(tapi, echo_url):
    slug = f"httpoff-{uid()}"
    create(tapi, slug, network=False, http_tools=[get_item(echo_url)])
    final, evs = run(tapi, slug, {"tool": "get_item", "args": {"id": "1"}})
    assert final["status"] == "succeeded"
    assert "network access is disabled" in results(evs)[0]["output"]

    tapi.put(f"/agents/{slug}/secrets", json={"API_KEY": SECRET})
    r = tapi.post(f"/agents/{slug}/tools/test", json={"tool": get_item(echo_url), "args": {"id": "7"},
                                                       "network": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == 200 and not out["is_error"] and '"path": "/items/7"' in out["output"]
    assert SECRET not in out["output"] and "[REDACTED]" in out["output"]
    bad = {**get_item(echo_url), "parameters": {"type": "object", "properties": {"id": {"type": "nope"}}}}
    assert tapi.post(f"/agents/{slug}/tools/test", json={"tool": bad, "args": {}}).status_code == 400


def test_http_tool_to_loopback_is_blocked_by_default(api, echo_url):
    """SSRF: without FORGE_ALLOW_PRIVATE_HTTP the default server refuses a request to 127.0.0.1."""
    slug = f"ssrf-{uid()}"
    r = api.post("/agents", json={"slug": slug, "name": "ssrf", "system_prompt": "t", "model": "fake:tools",
                                  "network": True, "http_tools": [get_item(echo_url)]})
    assert r.status_code == 200, r.text
    api.put(f"/agents/{slug}/secrets", json={"API_KEY": SECRET})
    final, evs = run(api, slug, {"tool": "get_item", "args": {"id": "1"}})
    out = results(evs)[0]
    assert final["status"] == "succeeded" and out["is_error"] and "blocked request to 127.0.0.1" in out["output"]


# ---------------------------------------------------------------- MCP


def test_mcp_stdio_server_runs_inside_the_sandbox(tapi):
    """The stdio server is started with `docker exec -i` in the run's container (cwd /workspace, no network)."""
    slug = f"mcpio-{uid()}"
    server = {"name": "local", "transport": "stdio", "command": "python", "args": ["mcp_server.py", "stdio"],
              "env": {"TEST_TOKEN": "{{API_KEY}}"}}
    create(tapi, slug, network=False, mcp_servers=[server], seed_files={"mcp_server.py": FIXTURE.read_text()})
    tapi.put(f"/agents/{slug}/secrets", json={"API_KEY": SECRET})

    listed = tapi.post(f"/agents/{slug}/mcp/list", json={"server": server})
    assert listed.status_code == 200, listed.text
    assert "mcp__local__add" in {t["name"] for t in listed.json()["tools"]}

    final, evs = run(tapi, slug, {"calls": [
        {"tool": "mcp__local__add", "args": {"a": 2, "b": 3}}, {"tool": "mcp__local__where"},
        {"tool": "mcp__local__picture"}, {"tool": "mcp__local__whoami"}, {"tool": "mcp__local__boom"}]})
    assert final["status"] == "succeeded"
    add, where, pic, who, boom = results(evs)
    assert add["output"] == "5" and add["source"] == "mcp" and add["mcp_server"] == "local"
    assert where["output"] == "cwd=/workspace"
    assert pic["output"] == "[image]\na 1px picture"
    assert who["output"] == "token=[REDACTED]"
    assert boom["is_error"]
    assert SECRET not in json.dumps(evs)


def test_mcp_http_server_and_failing_server(tapi, mcp_http_url):
    """Remote Streamable HTTP server with a secret header; a server that cannot start is reported and skipped."""
    slug = f"mcphttp-{uid()}"
    servers = [{"name": "remote", "transport": "http", "url": mcp_http_url,
                "headers": {"X-Test-Token": "{{API_KEY}}"}},
               {"name": "dead", "transport": "stdio", "command": "no-such-mcp-binary"}]
    create(tapi, slug, network=True, mcp_servers=servers)
    tapi.put(f"/agents/{slug}/secrets", json={"API_KEY": SECRET})
    final, evs = run(tapi, slug, {"calls": [{"tool": "mcp__remote__whoami"},
                                            {"tool": "mcp__remote__echo", "args": {"text": "over http"}}]})
    assert final["status"] == "succeeded"
    who, echo = results(evs)
    assert who["output"] == "token=[REDACTED]" and echo["output"] == "over http"
    errors = [e["payload"]["message"] for e in evs if e["type"] == "error"]
    assert any("'dead' failed to start" in m for m in errors), errors
    listed = tapi.post(f"/agents/{slug}/mcp/list", json={"server": servers[0]})
    assert listed.status_code == 200 and "mcp__remote__whoami" in {t["name"] for t in listed.json()["tools"]}
    bad = tapi.post(f"/agents/{slug}/mcp/list", json={"server": servers[1]})
    assert bad.status_code == 502 and "could not list tools" in bad.json()["detail"]


# ---------------------------------------------------------------- agents calling agents


def test_parent_calls_child_with_rollup_and_links(tapi):
    """A child run of the callee's production version: linked both ways, usage rolled into the parent."""
    child_slug, parent_slug = f"child-{uid()}", f"parent-{uid()}"
    create(tapi, child_slug, model="fake:echo", description="Echoes what it is told.")
    create(tapi, parent_slug, callable_agents=[child_slug])
    final, evs = run(tapi, parent_slug, {"tool": f"agent__{child_slug}", "args": {"input": "hello child"}})
    assert final["status"] == "succeeded"
    call = next(e["payload"] for e in evs if e["type"] == "tool_call")
    assert call["name"] == f"agent__{child_slug}"
    res = results(evs)[0]
    child_id = res["child_run_id"]
    assert res["source"] == "agent" and not res["is_error"]
    assert res["output"].startswith("echo: hello child") and child_id in res["output"]

    child = tapi.get(f"/runs/{child_id}").json()
    assert child["source"] == "agent" and child["parent_run_id"] == final["id"]
    assert child["status"] == "succeeded" and child["input"] == "hello child"
    parent = tapi.get(f"/runs/{final['id']}").json()
    assert parent["parent_run_id"] is None
    assert parent["child_usage"]["tokens_in"] == child["tokens_in"] > 0
    assert parent["child_usage"]["tokens_out"] == child["tokens_out"]
    assert parent["tokens_in"] == 20  # its own two model turns only: children are never double counted

    bad = tapi.post("/agents", json={"slug": f"self-{uid()}", "name": "s", "system_prompt": "t",
                                     "callable_agents": ["does-not-exist"]})
    assert bad.status_code == 400 and "does not exist" in bad.json()["detail"]


def test_cycle_between_two_agents_is_refused(tapi):
    a_slug, b_slug = f"cyca-{uid()}", f"cycb-{uid()}"
    create(tapi, b_slug)
    create(tapi, a_slug, callable_agents=[b_slug])
    v = tapi.post(f"/agents/{b_slug}/versions", json={"callable_agents": [a_slug]}).json()
    assert tapi.post(f"/agents/{b_slug}/promote", json={"version": v["n"]}).status_code == 200
    assert tapi.post(f"/agents/{b_slug}/versions", json={"callable_agents": [b_slug]}).status_code == 400

    inner = json.dumps({"tool": f"agent__{a_slug}", "args": {"input": "loop back"}})
    final, evs = run(tapi, a_slug, {"tool": f"agent__{b_slug}", "args": {"input": inner}})
    assert final["status"] == "succeeded"
    b_run = results(evs)[0]["child_run_id"]
    b_res = results(events(tapi, b_run))[0]
    assert b_res["is_error"] and "cycle detected" in b_res["output"]
    assert "child_run_id" not in b_res
