"""Unit tests for the assistant's agent creation and deployment tools (forge.copilot.agent_tools): list_models,
draft_agent, create_agent, add_example_agents, get_deploy_info, add_http_tool, add_mcp_server, rollback_version.
Validation before asking, confirmation for every write, and the happy paths through the in-process API."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from forge import db, models_available
from forge.copilot import agent_tools, engine, tools
from forge.copilot.api_client import ForgeApi, ToolError
from forge.types import ModelResponse, ToolCall
from tests.test_copilot import chat, client, of_type, run_with_model  # noqa: F401  (client is a fixture)

NEW_READS = {"list_models", "draft_agent", "get_deploy_info"}
NEW_WRITES = {"create_agent", "add_example_agents", "add_http_tool", "add_mcp_server", "rollback_version"}


class Script:
    """A model that makes the given tool calls on its first step, then says `done`."""

    def __init__(self, *calls: tuple[str, dict]):
        self.calls = calls

    async def complete(self, system, messages, tools_, temperature=None):
        if messages[-1].role == "user":
            return ModelResponse(tool_calls=[ToolCall(id=f"c{i}", name=n, args=a)
                                             for i, (n, a) in enumerate(self.calls)])
        return ModelResponse(text="done")


def ctx_for(client) -> tools.ToolContext:
    return tools.ToolContext(api=ForgeApi(local=True, app=client.app))


def describe(client, name: str, args: dict) -> str:
    async def go() -> str:
        return await tools.TOOLS[name].describe(ctx_for(client), args)
    return client.portal.call(go)


def run_approved(client, name: str, args: dict) -> tuple[tools.ToolResult, bool]:
    async def go():
        return await engine.execute(tools.TOOLS[name], args, ctx_for(client), [], approved=True)
    return client.portal.call(go)


def outputs(events: list[dict]) -> str:
    return json.dumps([e for e in events if e["type"] in ("tool_result", "assistant", "confirm_request")])


# ---------------------------------------------------------------- registry and model checks


def test_new_tools_are_registered_with_their_risk():
    for name in NEW_READS:
        assert tools.TOOLS[name].risk == "read", name
    for name in NEW_WRITES:
        assert tools.TOOLS[name].risk == "write" and tools.TOOLS[name].describe is not None, name
    # no argument can carry a secret value
    for name in NEW_READS | NEW_WRITES:
        assert not {"secrets", "secret", "api_key"} & set(tools.TOOLS[name].parameters["properties"]), name


@pytest.mark.parametrize("name", sorted(NEW_WRITES))
def test_every_new_write_tool_needs_approval(name):
    with pytest.raises(PermissionError):
        asyncio.run(engine.execute(tools.TOOLS[name], {}, tools.ToolContext(api=ForgeApi(local=True)), [],
                                   approved=False))


@pytest.fixture
def local_models(monkeypatch):
    """One reachable Ollama endpoint with qwen3:4b-instruct installed, and Claude with one model."""
    async def openai_status(base_url, name=None, refresh=False):
        return {"id": f"openai_compat:{base_url}", "name": "Ollama", "kind": "openai_compat", "base_url": base_url,
                "prefix": f"openai_compat:{base_url}|", "reachable": True, "error": None,
                "models": ["qwen3:4b-instruct", "qwen3:8b"]}

    async def anthropic_status(refresh=False):
        return {"id": "anthropic", "name": "Claude", "kind": "anthropic", "prefix": "anthropic:", "configured": True,
                "reachable": True, "error": None, "models": ["claude-sonnet-5-5"]}

    monkeypatch.setattr(models_available, "openai_status", openai_status)
    monkeypatch.setattr(models_available, "anthropic_status", anthropic_status)


def test_check_model_accepts_only_models_the_app_can_run(local_models):
    ok = "openai_compat:http://localhost:11434/v1|qwen3:4b-instruct"
    assert asyncio.run(agent_tools.check_model(ok)) == ok
    assert asyncio.run(agent_tools.check_model("anthropic:claude-sonnet-5-5")) == "anthropic:claude-sonnet-5-5"
    assert asyncio.run(agent_tools.check_model("fake:echo")) == "fake:echo"
    for bad, why in (("gpt-4o", "must be"), ("openai_compat:http://localhost:11434/v1", "must be"),
                     ("openai_compat:http://localhost:11434/v1|llama9:70b", "not available"),
                     ("anthropic:claude-imaginary", "not available")):
        with pytest.raises(ToolError, match=why):
            asyncio.run(agent_tools.check_model(bad))


def test_check_model_rejects_claude_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ToolError, match="No Claude API key"):
        asyncio.run(agent_tools.check_model("anthropic:claude-sonnet-5-5"))


def test_list_models_shows_installed_models_and_hides_never_offered_ones(client, local_models, monkeypatch):
    from forge import settings

    monkeypatch.setattr(settings, "endpoints", lambda: [{"name": "Ollama", "base_url": "http://localhost:11434/v1"}])
    res, ok = run_approved(client, "list_models", {})
    assert ok, res.text
    assert "openai_compat:http://localhost:11434/v1|qwen3:4b-instruct" in res.text
    assert "anthropic:claude-sonnet-5-5" in res.text and "server default" in res.text
    assert "qwen3:8b" not in res.text


# ---------------------------------------------------------------- draft then create


def test_make_an_agent_drafts_then_creates_after_one_confirmation(client):
    sid, ev = chat(client, "make me an agent that triages GitHub issues", answer=True, context={"page": "/"})
    results = of_type(ev, "tool_result")
    assert results[0]["tool"] == "draft_agent" and results[0]["ok"] and results[0]["risk"] == "read"
    req = of_type(ev, "confirm_request")
    assert len(req) == 1 and req[0]["tool"] == "create_agent"
    assert req[0]["summary"].startswith("Create agent issue-triager")
    assert "4 starter eval cases" in req[0]["summary"]
    created = results[-1]
    assert created["tool"] == "create_agent" and created["ok"], created
    assert created["refresh"] == ["agents", "agent:issue-triager"]
    assert {"label": "Deploy", "href": "/agents/issue-triager/deploy"} in created["links"]
    assert created["links"][0]["href"] == "/agents/issue-triager/configure"
    agent = client.get("/v1/agents/issue-triager").json()
    assert agent["prod_version"] == 1 and len(client.get("/v1/agents/issue-triager/evals").json()) == 4
    assert agent["versions"][0]["network"] is True
    assert "fk_" not in outputs(ev)  # the new agent's API key never reaches the chat
    assert "GITHUB_TOKEN" in results[0]["output"] and "Configure tab" in results[0]["output"]
    assert "/agents/issue-triager/configure" in of_type(ev, "assistant")[-1]["text"]


def test_declining_the_draft_creates_nothing(client):
    _, ev = chat(client, "make me an agent that triages GitHub issues", answer=False, context={"page": "/"})
    assert of_type(ev, "confirm_resolved")[0]["approved"] is False
    assert client.get("/v1/agents/issue-triager").status_code == 404
    assert "Nothing was created" in of_type(ev, "assistant")[-1]["text"]


def test_create_agent_validates_before_asking(client, local_models):
    base = {"slug": "new-agent", "name": "New", "system_prompt": "You help."}
    cases = [({**base, "slug": "Bad Slug"}, "slug must be"),
             ({**base, "slug": "s"}, "already exists"),
             ({**base, "tools": ["bash", "browser"]}, "subset"),
             ({**base, "seed_files": {"../etc/passwd": "x"}}, "relative"),
             ({**base, "seed_files": {"a.txt": 3}}, "text"),
             ({**base, "model": "openai_compat:http://localhost:11434/v1|not-pulled"}, "not available"),
             ({**base, "suggested_prompts": [{"title": "", "prompt": "x"}]}, "title"),
             ({**base, "eval_cases": [{"name": "a", "input": "b", "checker_type": "regex", "checker": "c"}]},
              "checker_type")]
    for args, why in cases:
        with pytest.raises(ToolError, match=why):
            describe(client, "create_agent", args)
    assert "unknown" in tools.validate_args(tools.TOOLS["create_agent"], {**base, "secrets": {"A": "b"}})
    ev = run_with_model(client, Script(("create_agent", {**base, "slug": "Bad Slug"})), "create it")
    assert of_type(ev, "confirm_request") == [] and "slug must be" in of_type(ev, "tool_result")[0]["output"]
    assert client.get("/v1/agents/new-agent").status_code == 404


def test_create_agent_happy_path_with_model_seed_files_and_prompts(client, local_models):
    args = {"slug": "notes-bot", "name": "Notes bot", "description": "Keeps notes.", "system_prompt": "You keep notes.",
            "model": "openai_compat:http://localhost:11434/v1|qwen3:4b-instruct", "tools": ["read_file", "write_file"],
            "network": False, "seed_files": {"notes/README.md": "# Notes"},
            "suggested_prompts": [{"title": "Summarize", "prompt": "Summarize notes/README.md"}]}
    summary = describe(client, "create_agent", args)
    assert "qwen3:4b-instruct" in summary and "1 seed files" in summary and "network off" in summary
    res, ok = run_approved(client, "create_agent", args)
    assert ok, res.text
    assert "fk_" not in res.text and "Deploy tab" in res.text
    a = client.get("/v1/agents/notes-bot").json()
    v = a["versions"][0]
    assert v["model"] == args["model"] and v["tools"] == ["read_file", "write_file"]
    assert v["seed_files"] == {"notes/README.md": "# Notes"} and a["suggested_prompts"][0]["title"] == "Summarize"


# ---------------------------------------------------------------- examples


def test_add_one_example_agent_then_it_is_already_installed(client):
    assert "Add 1 example agent" in describe(client, "add_example_agents", {"key": "log-triage"})
    res, ok = run_approved(client, "add_example_agents", {"key": "log-triage"})
    assert ok, res.text
    assert "fk_" not in res.text and res.refresh[0] == "agents"
    slug = next(e["agent_slug"] for e in client.get("/v1/examples").json() if e["key"] == "log-triage")
    assert client.get(f"/v1/agents/{slug}").status_code == 200
    with pytest.raises(ToolError, match="already installed"):
        describe(client, "add_example_agents", {"key": "log-triage"})
    with pytest.raises(ToolError, match="no example"):
        describe(client, "add_example_agents", {"key": "nope"})


def test_add_all_example_agents_through_the_chat(client):
    n = len(client.get("/v1/examples").json())
    _, ev = chat(client, "add the example agents", answer=True, context={"page": "/"})
    req = of_type(ev, "confirm_request")
    assert len(req) == 1 and req[0]["tool"] == "add_example_agents" and f"Add {n} example agents" in req[0]["summary"]
    assert of_type(ev, "tool_result")[0]["ok"]
    assert all(e["installed"] for e in client.get("/v1/examples").json())
    assert "fk_" not in outputs(ev)
    with pytest.raises(ToolError, match="already installed"):
        describe(client, "add_example_agents", {})


# ---------------------------------------------------------------- deploy info


def test_deploy_info_reads_at_once_and_keeps_secrets_out_of_the_chat(client):
    token = client.get("/v1/agents/s").json()["share_token"]
    _, ev = chat(client, "how do I call s", context={"page": "/agents/s/deploy", "agent_slug": "s"})
    assert of_type(ev, "confirm_request") == []
    r = of_type(ev, "tool_result")[0]
    assert r["tool"] == "get_deploy_info" and r["ok"]
    assert "/v1/agents/s/runs" in r["output"] and "$FORGE_API_KEY" in r["output"]
    assert token not in r["output"] and client.api_key not in r["output"]  # type: ignore[attr-defined]
    assert {"label": "Share link", "href": f"/s/s?share={token}"} in r["links"]  # for the user only
    assert token not in of_type(ev, "assistant")[-1]["text"]


def test_deploy_info_uses_the_public_url(client, monkeypatch):
    from forge import settings

    real = settings.get
    monkeypatch.setattr(settings, "get", lambda k, *a, **kw: "https://forge.example.com" if k == "public_url"
                        else real(k, *a, **kw))
    res, ok = run_approved(client, "get_deploy_info", {"slug": "s"})
    assert ok and "POST https://forge.example.com/v1/agents/s/runs" in res.text and "$FORGE_URL" not in res.text


# ---------------------------------------------------------------- HTTP tools, MCP servers, rollback


def test_add_http_tool_refuses_literal_credentials(client):
    base = {"slug": "s", "name": "get_weather", "description": "Weather by city.",
            "url": "https://api.example.com/weather/{city}", "method": "GET",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}
    for extra in ({"headers": {"Authorization": "Bearer abcdef1234567890abcd"}},
                  {"headers": {"X-Api-Key": "plainvalue"}},
                  {"headers": {"X-Thing": "sk-abcdefghijklmnop"}},
                  {"url": "https://api.example.com/weather/{city}?api_key=xyz"}):
        with pytest.raises(ToolError, match="cannot set secret values|credential"):
            describe(client, "add_http_tool", {**base, **extra})
    with pytest.raises(ToolError, match="placeholder"):
        describe(client, "add_http_tool", {**base, "url": "https://api.example.com/{town}"})


def test_add_http_tool_creates_an_unpromoted_version(client):
    args = {"slug": "s", "name": "get_weather", "description": "Weather by city.",
            "url": "https://api.example.com/weather/{city}", "method": "GET",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            "headers": {"Authorization": "Bearer {{WEATHER_TOKEN}}"}}
    assert describe(client, "add_http_tool", args).startswith("Create v2 of s from prod v1 adding HTTP tool get_weather")
    res, ok = run_approved(client, "add_http_tool", args)
    assert ok, res.text
    a = client.get("/v1/agents/s").json()
    assert a["prod_version"] == 1
    v2 = next(v for v in a["versions"] if v["n"] == 2)
    assert v2["http_tools"][0]["name"] == "get_weather" and v2["system_prompt"] == "You help."
    client.post("/v1/agents/s/promote", json={"version": 2})
    with pytest.raises(ToolError, match="twice"):  # validated against production's tools
        describe(client, "add_http_tool", args)


def test_add_mcp_server(client):
    with pytest.raises(ToolError, match="credential"):
        describe(client, "add_mcp_server", {"slug": "s", "name": "gh", "transport": "stdio", "command": "npx",
                                            "args": ["-y", "server-github"], "env": {"GITHUB_TOKEN": "ghp_abc"}})
    args = {"slug": "s", "name": "gh", "transport": "stdio", "command": "npx", "args": ["-y", "server-github"],
            "env": {"GITHUB_TOKEN": "{{GITHUB_TOKEN}}"}}
    assert "Network is off" in describe(client, "add_mcp_server", args)
    res, ok = run_approved(client, "add_mcp_server", args)
    assert ok, res.text
    v2 = next(v for v in client.get("/v1/agents/s").json()["versions"] if v["n"] == 2)
    assert v2["mcp_servers"][0]["name"] == "gh"
    with pytest.raises(ToolError, match="URL must start"):
        describe(client, "add_mcp_server", {"slug": "s", "name": "web", "transport": "http", "url": "ftp://x"})


def test_rollback_promotes_the_previous_production_version(client):
    with pytest.raises(ToolError, match="no earlier version"):
        describe(client, "rollback_version", {"slug": "s"})
    client.post("/v1/agents/s/versions", json={"system_prompt": "v2"})
    client.post("/v1/agents/s/versions", json={"system_prompt": "v3"})
    client.post("/v1/agents/s/promote", json={"version": 3})
    assert "make v1 production again (currently v3)" in describe(client, "rollback_version", {"slug": "s"})
    res, ok = run_approved(client, "rollback_version", {"slug": "s"})
    assert ok and client.get("/v1/agents/s").json()["prod_version"] == 1


def test_create_version_checks_the_model(client, local_models):
    with pytest.raises(ToolError, match="not available"):
        describe(client, "create_version", {"slug": "s", "model": "openai_compat:http://localhost:11434/v1|nope"})


def test_fake_copilot_routes_the_new_scenarios():
    from forge.copilot import fake
    from forge.types import Message

    def first_call(text: str) -> tuple[str, dict[str, Any]]:
        r = fake.respond([Message(role="user", text=f"<context>\npage: /\n</context>\n\n{text}")])
        return r.tool_calls[0].name, r.tool_calls[0].args

    assert first_call("Make me an agent that triages GitHub issues")[0] == "draft_agent"
    assert first_call("add the example agents") == ("add_example_agents", {})
    assert first_call("how do I call repo-maintainer") == ("get_deploy_info", {"slug": "repo-maintainer"})
