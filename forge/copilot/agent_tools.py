"""Assistant tools that create and deploy agents (see CONTRACT.md "Assistant"): list_models, draft_agent,
create_agent, add_example_agents, get_deploy_info, add_http_tool, add_mcp_server and rollback_version.

Same rules as forge.copilot.tools: thin wrappers over the HTTP API (POST /agents, POST /agent-drafts,
POST /examples/..., GET /agents/{slug}, POST /agents/{slug}/versions, POST /agents/{slug}/promote), write tools
validate in `describe` before the user is asked, and nothing here reads or sets a secret value. API keys returned
by agent creation are dropped (never shown to the model); the share link goes only into the result's links for
the user, never into the text the model sees. Imported at the end of forge.copilot.tools, which registers them.
"""

from __future__ import annotations

import json
import re
from typing import Any

from forge import db
from forge.copilot.api_client import ToolError
from forge.copilot.tools import (SLUG, ToolContext, ToolResult, agent_path, agent_row, clip, need, prod_n, tool)

BUILTIN_TOOLS = ["bash", "read_file", "write_file", "edit_file"]
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
NEVER_OFFER = re.compile(r"\|qwen3:8b$")  # mirrors web/src/api.ts NEVER_OFFER: never suggested as a preset
MAX_SEED_FILES, MAX_SEED_CHARS = 50, 500_000
MAX_PROMPTS, MAX_CASES = 12, 10
# Header and env names that carry credentials: their values must reference a secret as {{NAME}}.
SENSITIVE_NAME = re.compile(r"auth|token|key|secret|password|passwd|cookie|session|credential", re.I)
# Values that look like a pasted credential, whatever the header is called.
LITERAL_CREDENTIAL = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_|gho_|github_pat_|xox[abpr]-|AKIA[0-9A-Z]{8,})|"
                                r"\bBearer\s+(?!\{\{)[A-Za-z0-9._~+/=-]{12,}", re.I)
NO_SECRETS = ("the assistant cannot set secret values: reference a secret by name as {{NAME}} and set its value "
              "on the agent's Configure tab")


# ---------------------------------------------------------------- shared checks


async def check_model(model: Any) -> str:
    """The model string when the app can run it now; ToolError otherwise. Same rule as the New agent form's
    warning (forge.models_available.model_warning), but blocking: a Claude model must be available to the
    configured key, a local model must be installed on its reachable endpoint."""
    from forge.models_available import model_warning

    m = str(model or "").strip()
    kind, _, rest = m.partition(":")
    if kind not in ("anthropic", "openai_compat", "fake") or not rest or (kind == "openai_compat" and "|" not in rest):
        raise ToolError("model must be anthropic:<id> or openai_compat:<base_url>|<model>; call list_models for "
                        "the models this Forge can run, or omit model for the server default")
    if warning := await model_warning(m):
        raise ToolError(f"{warning.rstrip('.')}. Call list_models and pick an available model, or omit model for "
                        "the server default.")
    return m


def check_secret_free(kind: str, values: dict[str, Any] | None) -> None:
    for k, v in (values or {}).items():
        if not isinstance(v, str):
            raise ToolError(f"{kind} {k!r} must be a string")
        if "{{" in v:
            continue
        if SENSITIVE_NAME.search(str(k)) or LITERAL_CREDENTIAL.search(v):
            raise ToolError(f"{kind} {k!r} looks like a credential; {NO_SECRETS}")


def check_seed_files(files: Any) -> dict[str, str]:
    if files is None:
        return {}
    if not isinstance(files, dict):
        raise ToolError("seed_files must be an object of path to text content")
    if len(files) > MAX_SEED_FILES:
        raise ToolError(f"at most {MAX_SEED_FILES} seed files")
    total = 0
    for path, content in files.items():
        p = str(path)
        if not p or p.startswith(("/", "~")) or "\\" in p or any(part in ("", ".", "..") for part in p.split("/")):
            raise ToolError(f"seed file path {p!r} must be relative, without .. or empty parts")
        if not isinstance(content, str):
            raise ToolError(f"seed file {p!r} must be text")
        total += len(content)
    if total > MAX_SEED_CHARS:
        raise ToolError(f"seed files are {total} characters; keep them under {MAX_SEED_CHARS}")
    return files


def check_prompts(prompts: Any) -> list[dict]:
    if prompts is None:
        return []
    if not isinstance(prompts, list) or len(prompts) > MAX_PROMPTS:
        raise ToolError(f"suggested_prompts must be a list of at most {MAX_PROMPTS}")
    for p in prompts:
        if not isinstance(p, dict) or set(p) - {"title", "prompt"} or not str(p.get("title") or "").strip() \
                or not str(p.get("prompt") or "").strip() or len(str(p["title"])) > 80:
            raise ToolError("each suggested prompt needs a title (at most 80 characters) and a prompt")
    return prompts


def check_cases(cases: Any) -> list[dict]:
    if cases is None:
        return []
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        raise ToolError(f"eval_cases must be a list of at most {MAX_CASES}")
    for c in cases:
        if not isinstance(c, dict) or not all(isinstance(c.get(k), str) and c[k].strip()
                                              for k in ("name", "input", "checker")):
            raise ToolError("each eval case needs name, input, checker_type and checker")
        if c.get("checker_type") not in ("shell", "judge") or c.get("split", "train") not in ("train", "holdout"):
            raise ToolError("eval case checker_type must be shell or judge, split train or holdout")
        if set(c) - {"name", "input", "checker_type", "checker", "split"}:
            raise ToolError("eval cases take only name, input, checker_type, checker and split")
    return cases


def onoff(b: Any) -> str:
    return "on" if b else "off"


def next_n(agent: dict) -> int:
    return max((v["n"] for v in db.list_versions(agent["id"])), default=0) + 1


def agent_links(slug: str, name: str | None = None) -> list[dict[str, str]]:
    return [{"label": name or slug, "href": agent_path(slug, "configure")},
            {"label": "Playground", "href": agent_path(slug, "playground")},
            {"label": "Deploy", "href": agent_path(slug, "deploy")}]


# ---------------------------------------------------------------- read tools


@tool("list_models", "List the models this Forge can run now: Claude models for the configured key and models "
      "installed on local endpoints such as Ollama, plus the server default.", {}, [], "read")
async def list_models(ctx: ToolContext, args: dict) -> ToolResult:
    from forge.server.evals import default_model

    body = await ctx.api.get("/models/available")
    out = [f"server default (used when model is omitted): {default_model()}"]
    for p in body.get("providers") or []:
        if not p.get("reachable"):
            out.append(f"- {p['name']}: not available ({p.get('error') or 'unreachable'})")
            continue
        models = [p["prefix"] + m for m in p.get("models") or [] if not NEVER_OFFER.search(p["prefix"] + m)]
        shown = models[:30]
        more = f" and {len(models) - 30} more" if len(models) > 30 else ""
        out.append(f"- {p['name']}: {', '.join(shown) or 'no models installed'}{more}")
    n = sum(len(p.get("models") or []) for p in body.get("providers") or [] if p.get("reachable"))
    return ToolResult("\n".join(out), f"{n} models available", [{"label": "Settings", "href": "/settings"}])


@tool("draft_agent", "Have the grader model write a complete agent configuration (name, slug, instructions, tools, "
      "network, starter eval cases) from a plain description; one grader call, creates nothing. Then call "
      "create_agent with it.",
      {"description": {"type": "string", "description": "What the agent should do, in the user's words."},
       "examples": {"type": "array", "items": {"type": "string"}, "description": "Example tasks (optional)."},
       "constraints": {"type": "string", "description": "Constraints such as no network (optional)."}},
      ["description"], "read")
async def draft_agent(ctx: ToolContext, a: dict) -> ToolResult:
    body: dict[str, Any] = {"description": str(need(a, "description"))}
    if a.get("examples"):
        body["examples"] = [str(x) for x in a["examples"]][:10]
    if a.get("constraints"):
        body["constraints"] = str(a["constraints"])
    d = await ctx.api.post("/agent-drafts", body)
    config = {k: d[k] for k in ("slug", "name", "description", "system_prompt", "tools", "network") if k in d}
    config["eval_cases"] = [{k: c[k] for k in ("name", "input", "checker_type", "checker", "split") if k in c}
                            for c in d.get("eval_cases") or []]
    secrets = d.get("suggested_secrets") or []
    out = [f"Draft for {d['name']} ({d['slug']}): {d.get('description', '')}",
           f"tools: {', '.join(d.get('tools') or [])}; network {onoff(d.get('network'))}: "
           f"{d.get('network_reason') or ''}",
           f"starter eval cases: {len(config['eval_cases'])}",
           f"seed files hint: {d.get('seed_files_hint') or 'none'}"]
    if secrets:
        out.append("secrets it needs (names only; the user sets values on the Configure tab after creation, never "
                   "in chat): " + "; ".join(f"{s['name']} ({clip(s.get('why'), 100)})" for s in secrets))
    out += ["", "Show the user the draft in a few lines, then call create_agent with this config (edit it first "
            "if the user asked for changes; add model only if the user picked one):",
            "config: " + json.dumps(config)]
    return ToolResult("\n".join(out), f"drafted {d['slug']} with {len(config['eval_cases'])} starter cases")


@tool("get_deploy_info", "Show how to call an agent: its runs endpoint, a curl example and where the API key and "
      "share link live. The API key is never shown; the share link is given to the user as a link.",
      {"slug": SLUG}, ["slug"], "read")
async def get_deploy_info(ctx: ToolContext, a: dict) -> ToolResult:
    from forge import settings

    slug = need(a, "slug")
    ag = await ctx.api.get(f"/agents/{slug}")
    public = str(settings.get("public_url") or "").rstrip("/")
    origin = public or "$FORGE_URL"
    curl = (f"curl -s {origin}/v1/agents/{slug}/runs \\\n  -H \"Authorization: Bearer $FORGE_API_KEY\" \\\n"
            f"  -H \"Content-Type: application/json\" \\\n  -d '{{\"input\": \"Describe the task here\", \"wait\": true}}'")
    out = [f"agent: {slug} ({ag['name']}), production v{ag.get('prod_version')}",
           f"runs endpoint: POST {origin}/v1/agents/{slug}/runs (Authorization: Bearer <api_key>); the reply is a "
           "Run with status and output",
           f"conversations: POST {origin}/v1/agents/{slug}/sessions, then POST {origin}/v1/sessions/<id>/messages",
           "curl:", curl,
           "API key: shown only once, when the agent was created; it is never shown here. Rotate it on the Deploy "
           "tab to get a new one (the old key stops working).",
           "share link: given to the user as a link under this result (the token stays out of the chat); colleagues "
           "chat with the production version there."]
    if not public:
        out.insert(2, "FORGE_URL is the address the user opens Forge at (for example http://localhost:8000); set "
                      "public_url in Settings to fill it in.")
    links = [{"label": "Deploy tab", "href": agent_path(slug, "deploy")}]
    if ag.get("share_token"):  # only for the user's UI, never in the text the model sees
        links.append({"label": "Share link", "href": f"/s/{slug}?share={ag['share_token']}"})
    return ToolResult("\n".join(out), f"deploy info for {slug}", links)


# ---------------------------------------------------------------- write tools: confirmation summaries


async def _d_create_agent(ctx: ToolContext, a: dict) -> str:
    slug = str(need(a, "slug"))
    if not SLUG_RE.match(slug):
        raise ToolError("slug must be lowercase letters, digits and dashes (at most 63 characters)")
    if db.get_agent_by_slug(slug):
        raise ToolError(f"agent {slug!r} already exists; pick another slug")
    if len(str(need(a, "name"))) > 80:
        raise ToolError("name must be at most 80 characters")
    if len(str(need(a, "system_prompt"))) > 20_000:
        raise ToolError("system_prompt must be at most 20000 characters")
    tools_ = a.get("tools")
    if tools_ is not None and (set(tools_) - set(BUILTIN_TOOLS)):
        raise ToolError(f"tools must be a subset of {BUILTIN_TOOLS}")
    files, prompts, cases = check_seed_files(a.get("seed_files")), check_prompts(a.get("suggested_prompts")), \
        check_cases(a.get("eval_cases"))
    model = await check_model(a["model"]) if a.get("model") else None
    from forge.server.evals import default_model

    extras = [f"{len(files)} seed files" if files else "", f"{len(cases)} starter eval cases" if cases else "",
              f"{len(prompts)} suggested prompts" if prompts else "", "auto promote on" if a.get("auto_promote") else ""]
    return (f"Create agent {slug} (\"{clip(a['name'], 60)}\") with model {model or default_model() + ' (default)'}, "
            f"tools {', '.join(tools_ if tools_ is not None else BUILTIN_TOOLS) or 'none'}, network "
            f"{onoff(a.get('network'))}" + "".join(f", {x}" for x in extras if x)
            + f". Instructions: {clip(a['system_prompt'], 100)} Free until it runs.")


async def _d_examples(ctx: ToolContext, a: dict) -> str:
    exs = await ctx.api.get("/examples")
    model = await check_model(a["model"]) if a.get("model") else None
    with_model = f" with model {model}" if model else " with each example's own model or the server default"
    if a.get("key"):
        ex = next((e for e in exs if e["key"] == a["key"] or e["agent_slug"] == a["key"]), None)
        if ex is None:
            raise ToolError(f"no example {a['key']!r}; examples: {', '.join(e['key'] for e in exs)}")
        if ex["installed"]:
            raise ToolError(f"example {ex['key']} is already installed as {ex['agent_slug']}")
        todo = [ex]
    else:
        todo = [e for e in exs if not e["installed"]]
        if not todo:
            raise ToolError("every example agent is already installed")
    names = "; ".join(f"{e['agent_slug']} ({e['eval_cases']} eval cases"
                      f"{', needs network' if e.get('needs_network') else ''})" for e in todo)
    return f"Add {len(todo)} example agent{'s' if len(todo) != 1 else ''}{with_model}: {names}. Free until they run."


async def _d_rollback(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    prev = previous_prod(agent)
    return (f"Roll back {agent['slug']}: make v{prev['n']} production again (currently v{prod_n(agent)}). "
            f"v{prod_n(agent)} is kept and can be promoted again. Free.")


def previous_prod(agent: dict) -> dict:
    """The version that was production before the current one (from the promotion log), else the highest
    version number below production."""
    cur = agent["prod_version_id"]
    for p in reversed(db.list_promotions(agent["id"])):
        if p["version_id"] == cur and p.get("from_version_id") and p["from_version_id"] != cur:
            v = db.get_version(p["from_version_id"])
            if v:
                return v
    n = prod_n(agent) or 0
    older = [v for v in db.list_versions(agent["id"]) if v["n"] < n]
    if not older:
        raise ToolError(f"{agent['slug']} has no earlier version to roll back to")
    return max(older, key=lambda v: v["n"])


def _prod(agent: dict) -> dict:
    return db.get_version(agent["prod_version_id"]) or {}


def _new_http_tool(a: dict) -> dict:
    t = {"name": need(a, "name"), "description": need(a, "description"), "url": need(a, "url"),
         "method": a.get("method") or "POST",
         "parameters": a.get("parameters") or {"type": "object", "properties": {}}}
    if a.get("headers"):
        t["headers"] = a["headers"]
    if a.get("query_params"):
        t["query_params"] = a["query_params"]
    return t


def _new_mcp(a: dict) -> dict:
    s: dict[str, Any] = {"name": need(a, "name"), "transport": a.get("transport") or "http"}
    for k in ("url", "headers", "command", "args", "env"):
        if a.get(k):
            s[k] = a[k]
    return s


def _check_tool_config(agent: dict, http_tools: list[dict], mcp: list[dict]) -> None:
    from pydantic import ValidationError

    from forge.tools import validate_tool_config
    from forge.types import HttpTool, McpServer

    try:
        errs = validate_tool_config([HttpTool(**t) for t in http_tools], [McpServer(**m) for m in mcp], [],
                                    agent["slug"])
    except ValidationError as exc:
        raise ToolError(f"invalid tool: {clip(str(exc), 300)}") from None
    if errs:
        raise ToolError("; ".join(errs))


async def _d_http_tool(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    prod = _prod(agent)
    t = _new_http_tool(a)
    check_secret_free("header", t.get("headers"))
    if LITERAL_CREDENTIAL.search(t["url"]) or re.search(r"[?&](?:api_?key|token|secret|password)=(?!\{)", t["url"], re.I):
        raise ToolError(f"the URL carries a credential; {NO_SECRETS}")
    _check_tool_config(agent, [*(prod.get("http_tools") or []), t], prod.get("mcp_servers") or [])
    return (f"Create v{next_n(agent)} of {agent['slug']} from prod v{prod.get('n')} adding HTTP tool {t['name']} "
            f"({t['method']} {clip(t['url'], 100)}). Not promoted; free until it runs.")


async def _d_mcp(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    prod = _prod(agent)
    s = _new_mcp(a)
    check_secret_free("header", s.get("headers"))
    check_secret_free("env", s.get("env"))
    if any(LITERAL_CREDENTIAL.search(str(x)) for x in [s.get("url", ""), *(s.get("args") or [])]):
        raise ToolError(f"the server config carries a credential; {NO_SECRETS}")
    _check_tool_config(agent, prod.get("http_tools") or [], [*(prod.get("mcp_servers") or []), s])
    where = s.get("url") or " ".join([s.get("command", ""), *(s.get("args") or [])])
    note = "" if s["transport"] != "stdio" or prod.get("network") else \
        " Network is off, so a stdio server that downloads packages will fail until network is on."
    return (f"Create v{next_n(agent)} of {agent['slug']} from prod v{prod.get('n')} adding MCP server {s['name']} "
            f"({s['transport']}: {clip(where, 100)}). Not promoted; free until it runs.{note}")


# ---------------------------------------------------------------- write tools


PROMPT_ITEM = {"type": "object", "properties": {"title": {"type": "string"}, "prompt": {"type": "string"}},
               "required": ["title", "prompt"], "additionalProperties": False}
CASE_ITEM = {"type": "object", "properties": {
    "name": {"type": "string"}, "input": {"type": "string"},
    "checker_type": {"type": "string", "enum": ["shell", "judge"]}, "checker": {"type": "string"},
    "split": {"type": "string", "enum": ["train", "holdout"]}},
    "required": ["name", "input", "checker_type", "checker"], "additionalProperties": False}


@tool("create_agent", "Create a new agent (production v1). Prefer draft_agent first to write the configuration; "
      "model must be one list_models shows, or omit it for the server default.",
      {"slug": {"type": "string", "description": "Lowercase letters, digits and dashes, unique."},
       "name": {"type": "string"}, "description": {"type": "string", "description": "One sentence."},
       "system_prompt": {"type": "string", "description": "The agent's instructions."},
       "model": {"type": "string", "description": "anthropic:<id> or openai_compat:<base_url>|<model>; omit for "
                 "the server default."},
       "tools": {"type": "array", "items": {"type": "string", "enum": BUILTIN_TOOLS},
                 "description": "Built-in tools; default all four."},
       "network": {"type": "boolean", "description": "Internet access in the sandbox; default off."},
       "seed_files": {"type": "object", "description": "Workspace files, relative path to text content."},
       "suggested_prompts": {"type": "array", "items": PROMPT_ITEM, "description": "Starter prompts (at most 12)."},
       "eval_cases": {"type": "array", "items": CASE_ITEM, "description": "Starter eval cases, e.g. from a draft."},
       "auto_promote": {"type": "boolean"}},
      ["slug", "name", "system_prompt"], "write", _d_create_agent)
async def create_agent(ctx: ToolContext, a: dict) -> ToolResult:
    body = {k: a[k] for k in ("slug", "name", "description", "system_prompt", "model", "tools", "network",
                              "seed_files", "suggested_prompts", "eval_cases", "auto_promote") if a.get(k) is not None}
    ag = await ctx.api.post("/agents", body)  # the response's api_key is dropped: never shown in chat
    out = [f"Created agent {ag['slug']} ({ag['name']}), production v{ag.get('prod_version')}."]
    if ag.get("warning"):
        out.append(f"Model warning: {ag['warning']}")
    if a.get("eval_cases"):
        out.append(f"{len(a['eval_cases'])} starter eval cases added.")
    out.append("Its API key is not shown in chat: rotate it on the Deploy tab to get one. Secrets it needs are set "
               "by the user on the Configure tab.")
    return ToolResult("\n".join(out), f"created {ag['slug']}", agent_links(ag["slug"], ag["name"]),
                      ["agents", f"agent:{ag['slug']}"])


@tool("add_example_agents", "Install one bundled example agent (by key or slug) or, without a key, every example "
      "not installed yet, like the Examples button on the Agents page.",
      {"key": {"type": "string", "description": "Example key or agent slug; omit for all."},
       "model": {"type": "string", "description": "Model for the installed agents; omit to keep their own."}},
      [], "write", _d_examples)
async def add_example_agents(ctx: ToolContext, a: dict) -> ToolResult:
    body = {"model": a["model"]} if a.get("model") else {}
    if a.get("key"):
        exs = await ctx.api.get("/examples")
        key = next((e["key"] for e in exs if a["key"] in (e["key"], e["agent_slug"])), a["key"])
        installed, skipped = [await ctx.api.post(f"/examples/{key}/install", body)], []
    else:
        r = await ctx.api.post("/examples/install-all", body)
        installed, skipped = r["installed"], r["skipped"]
    out = [f"Installed {g['slug']} ({g['name']}), {g.get('eval_cases_synced', 0)} eval cases" for g in installed]
    if skipped:
        out.append(f"Already installed: {', '.join(skipped)}")
    out.append("API keys are not shown in chat: rotate one on an agent's Deploy tab to get it.")
    links = [{"label": g["name"], "href": agent_path(g["slug"], "configure")} for g in installed]
    return ToolResult("\n".join(out), f"installed {len(installed)} example agents", links or
                      [{"label": "Agents", "href": "/"}], ["agents", *(f"agent:{g['slug']}" for g in installed)])


@tool("rollback_version", "Roll an agent back: promote the version that was production before the current one.",
      {"slug": SLUG}, ["slug"], "write", _d_rollback)
async def rollback_version(ctx: ToolContext, a: dict) -> ToolResult:
    prev = previous_prod(agent_row(a["slug"]))
    ag = await ctx.api.post(f"/agents/{a['slug']}/promote", {"version": prev["n"]})
    return ToolResult(f"{a['slug']} production is now v{ag['prod_version']} (rolled back).",
                      f"prod is v{ag['prod_version']}",
                      [{"label": "Versions", "href": agent_path(a["slug"], "configure")}], [f"agent:{a['slug']}"])


SECRET_REF = "Values may reference secrets only by name as {{NAME}}."


@tool("add_http_tool", "Add an HTTP tool to an agent as a new unpromoted version (production's config plus the "
      "tool). Credentials go in headers as {{SECRET_NAME}} references only.",
      {"slug": SLUG, "name": {"type": "string", "description": "Tool name the model sees, e.g. get_weather."},
       "description": {"type": "string"}, "url": {"type": "string", "description": "May use {param} placeholders."},
       "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
       "parameters": {"type": "object", "description": "JSON schema of the arguments (type object)."},
       "headers": {"type": "object", "description": f"Header name to value. {SECRET_REF}"},
       "query_params": {"type": "array", "items": {"type": "string"}},
       "rationale": {"type": "string"}},
      ["slug", "name", "description", "url"], "write", _d_http_tool)
async def add_http_tool(ctx: ToolContext, a: dict) -> ToolResult:
    prod = _prod(agent_row(a["slug"]))
    v = await ctx.api.post(f"/agents/{a['slug']}/versions", {
        "http_tools": [*(prod.get("http_tools") or []), _new_http_tool(a)],
        "rationale": a.get("rationale") or f"Added HTTP tool {a['name']} with the Forge assistant"})
    return ToolResult(f"Created v{v['n']} with HTTP tool {a['name']} (not promoted; promote v{v['n']} to use it).",
                      f"created v{v['n']}", [{"label": f"v{v['n']}", "href": agent_path(a["slug"], "configure")}],
                      [f"agent:{a['slug']}"])


@tool("add_mcp_server", "Add an MCP server to an agent as a new unpromoted version (production's config plus the "
      "server). Credentials go in headers or env as {{SECRET_NAME}} references only.",
      {"slug": SLUG, "name": {"type": "string", "description": "Letters, digits, - or _."},
       "transport": {"type": "string", "enum": ["http", "sse", "stdio"], "description": "Default http."},
       "url": {"type": "string", "description": "For http and sse."},
       "headers": {"type": "object", "description": f"For http and sse. {SECRET_REF}"},
       "command": {"type": "string", "description": "For stdio; runs inside the sandbox."},
       "args": {"type": "array", "items": {"type": "string"}},
       "env": {"type": "object", "description": f"For stdio. {SECRET_REF}"},
       "rationale": {"type": "string"}},
      ["slug", "name"], "write", _d_mcp)
async def add_mcp_server(ctx: ToolContext, a: dict) -> ToolResult:
    prod = _prod(agent_row(a["slug"]))
    v = await ctx.api.post(f"/agents/{a['slug']}/versions", {
        "mcp_servers": [*(prod.get("mcp_servers") or []), _new_mcp(a)],
        "rationale": a.get("rationale") or f"Added MCP server {a['name']} with the Forge assistant"})
    return ToolResult(f"Created v{v['n']} with MCP server {a['name']} (not promoted; promote v{v['n']} to use it).",
                      f"created v{v['n']}", [{"label": f"v{v['n']}", "href": agent_path(a["slug"], "configure")}],
                      [f"agent:{a['slug']}"])
