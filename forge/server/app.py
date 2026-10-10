"""Forge HTTP API (see CONTRACT.md). Run: `uv run uvicorn forge.server.app:app --port 8000`."""

from __future__ import annotations

import asyncio
import io
import json
import re
import tarfile
import time
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Literal

from dotenv import load_dotenv
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from forge import budget, db
from forge.policy import http_tool_names, parse_policy
from forge.provenance import lesson_provenance
from forge.server import assist, authoring, evals, files, observability, obslog, runs, sessions_api
from forge.server import examples as examples_api
from forge.server import governance as governance_routes
from forge.server.auth import authorize, require_local
from forge.server.templates import TEMPLATES
from forge.server.views import (agent_view, eval_case_view, event_view, feedback_view, improvement_view, run_view,
                                version_view)
from forge.triggers import api as triggers_api, scheduler as triggers_scheduler
from forge.types import TERMINAL_STATUSES, Event, HttpTool, McpServer

load_dotenv()

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
MAX_SEED_FILE = 1_000_000
TERMINAL = TERMINAL_STATUSES


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    obslog.configure()
    runs.reset_manager()
    db.fail_interrupted_runs()
    db.fail_interrupted_improvements()
    triggers_scheduler.start()
    yield
    await triggers_scheduler.stop()


app = FastAPI(title="Forge", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
api = APIRouter(prefix="/v1")

# ---------------------------------------------------------------- lookups


def agent_or_404(slug: str) -> dict:
    a = db.get_agent_by_slug(slug)
    if a is None:
        raise HTTPException(404, f"agent {slug!r} not found")
    return a


def version_or_404(agent: dict, n: int | str) -> dict:
    if n != "prod" and not str(n).isdigit():
        raise HTTPException(400, "version must be a number or 'prod'")
    v = db.get_version_n(agent["id"], n)
    if v is None:
        raise HTTPException(404, f"version {n} not found")
    return v


def run_and_agent(run_id: str, request: Request, *, allow_share: bool = True) -> tuple[dict, dict, str]:
    """The run, its agent and the caller source. A share token reaches only runs started through the share
    page (source "share"); any other run of the agent is a 404, so its id is not even confirmed."""
    r = db.get_run(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    agent = db.get_agent(r["agent_id"])
    assert agent is not None
    source = authorize(request, agent, allow_share=allow_share)
    if source == "share" and r["source"] != "share":
        raise HTTPException(404, "run not found")
    return r, agent, source


# ---------------------------------------------------------------- builder


class EvalCaseIn(BaseModel):
    name: str
    input: str
    seed_files: dict[str, str] = Field(default_factory=dict)
    checker_type: Literal["shell", "judge"]
    checker: str
    split: Literal["train", "holdout"] = "train"


class SuggestedPrompt(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=20_000)


MAX_SUGGESTED_PROMPTS = 12


class AgentCreate(BaseModel):
    slug: str
    name: str
    description: str = ""
    system_prompt: str
    model: str | None = None
    tools: list[str] | None = None
    http_tools: list[HttpTool] | None = None
    mcp_servers: list[McpServer] | None = None
    callable_agents: list[str] | None = None
    network: bool = False
    seed_files: dict[str, str] | None = None
    lessons: list[str] | None = None
    auto_promote: bool = False
    eval_cases: list[EvalCaseIn] | None = None  # created with the agent (e.g. starter cases from a draft)
    approval_policy: dict[str, Any] | None = None  # governance
    max_run_cost_usd: float | None = Field(None, ge=0)  # governance
    monthly_budget_usd: float | None = Field(None, ge=0)  # governance
    suggested_prompts: list[SuggestedPrompt] | None = Field(default=None, max_length=MAX_SUGGESTED_PROMPTS)


class AgentPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    auto_promote: bool | None = None
    eval_repeats: int | None = Field(None, ge=1, le=evals.MAX_EVAL_REPEATS)
    monthly_budget_usd: float | None = Field(None, ge=0)  # governance; explicit null removes the budget
    suggested_prompts: list[SuggestedPrompt] | None = Field(default=None, max_length=MAX_SUGGESTED_PROMPTS)


def checked_policy(raw: dict[str, Any] | None, http_tools: list | None = None) -> dict[str, Any] | None:
    """Validates an approval policy (400 when invalid) and returns it normalized. Rules may name the
    version's HTTP tools besides the builtins and mcp__ / agent__ tools."""
    if raw is None:
        return None
    try:
        return parse_policy(raw, tools=http_tool_names(http_tools)).model_dump(exclude_none=True)
    except ValueError as exc:
        raise HTTPException(400, f"invalid approval_policy: {exc}") from exc


class VersionCreate(BaseModel):
    system_prompt: str | None = None
    lessons: list[str] | None = None
    model: str | None = None
    tools: list[str] | None = None
    http_tools: list[HttpTool] | None = None
    mcp_servers: list[McpServer] | None = None
    callable_agents: list[str] | None = None
    network: bool | None = None
    seed_files: dict[str, str] | None = None
    rationale: str | None = None
    approval_policy: dict[str, Any] | None = None  # governance
    max_run_cost_usd: float | None = Field(None, ge=0)  # governance; explicit null removes the cap
    # Agent level, not versioned: when given, replaces the agent's starter prompts.
    suggested_prompts: list[SuggestedPrompt] | None = Field(default=None, max_length=MAX_SUGGESTED_PROMPTS)


class Promote(BaseModel):
    version: int


def check_tool_config(http_tools: list | None, mcp_servers: list | None, callable_agents: list[str] | None,
                      own_slug: str) -> None:
    """400 with every problem in a version's HTTP tools, MCP servers and callable agents."""
    from forge.tools import validate_tool_config

    errors = validate_tool_config([HttpTool(**t) if isinstance(t, dict) else t for t in http_tools or []],
                                  [McpServer(**m) if isinstance(m, dict) else m for m in mcp_servers or []],
                                  list(callable_agents or []), own_slug)
    for slug in callable_agents or []:
        if slug != own_slug and db.get_agent_by_slug(slug) is None:
            errors.append(f"callable agent {slug!r} does not exist")
    if errors:
        raise HTTPException(400, "; ".join(errors))


@api.post("/agents", dependencies=[Depends(require_local)])
async def create_agent(body: AgentCreate) -> dict:
    if not SLUG_RE.match(body.slug):
        raise HTTPException(400, "slug must be lowercase letters, digits and dashes")
    if db.get_agent_by_slug(body.slug):
        raise HTTPException(409, f"agent {body.slug!r} already exists")
    check_tool_config(body.http_tools, body.mcp_servers, body.callable_agents, body.slug)
    policy = checked_policy(body.approval_policy, body.http_tools)
    prompts = [p.model_dump() for p in body.suggested_prompts] if body.suggested_prompts else None
    agent, key = db.create_agent(body.slug, body.name, body.description, body.auto_promote, prompts)
    v = db.create_version(
        agent["id"], system_prompt=body.system_prompt, model=body.model or evals.default_model(), tools=body.tools,
        http_tools=[t.model_dump() for t in body.http_tools] if body.http_tools else None, network=body.network,
        mcp_servers=[m.model_dump() for m in body.mcp_servers] if body.mcp_servers else None,
        callable_agents=body.callable_agents or None, seed_files=body.seed_files, lessons=body.lessons,
        approval_policy=policy, max_run_cost_usd=body.max_run_cost_usd)
    if body.lessons:
        db.set_lesson_meta(v["id"], lesson_provenance([], {}, v["lessons"], [], v["n"], origin="human"))
    for c in body.eval_cases or []:
        db.create_eval_case(agent["id"], c.name, c.input, c.checker_type, c.checker, c.split, c.seed_files)
    agent = db.set_prod_version(agent["id"], v["id"], "create")
    if body.monthly_budget_usd is not None:
        agent = db.update_agent(agent["id"], monthly_budget_usd=body.monthly_budget_usd)
    return {**agent_view(agent), "api_key": key}  # type: ignore[arg-type]


@api.get("/agents", dependencies=[Depends(require_local)])
async def list_agents() -> list[dict]:
    return [agent_view(a) for a in db.list_agents()]


@api.get("/agents/{slug}")
async def get_agent(slug: str, request: Request) -> dict:
    agent = agent_or_404(slug)
    if authorize(request, agent) == "share":
        # Share-link visitors see only what the share page displays, never configuration.
        # Suggested prompts are written for users of the agent, so they are shown there too.
        return {k: agent[k] for k in ("id", "slug", "name", "description", "suggested_prompts")}
    return agent_view(agent, versions=True)


@api.patch("/agents/{slug}")
async def patch_agent(slug: str, body: AgentPatch, request: Request) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    fields = body.model_dump(exclude_none=True)
    if "monthly_budget_usd" in body.model_fields_set:
        fields["monthly_budget_usd"] = body.monthly_budget_usd
    agent = db.update_agent(agent["id"], **fields)
    return agent_view(agent)  # type: ignore[arg-type]


@api.post("/agents/{slug}/versions")
async def create_version(slug: str, body: VersionCreate, request: Request) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    prod = version_or_404(agent, "prod")
    fields = {k: prod.get(k) for k in ("system_prompt", "lessons", "model", "tools", "http_tools", "network",
                                       "seed_files", "approval_policy", "max_run_cost_usd", "mcp_servers",
                                       "callable_agents")}
    # suggested_prompts is agent level, never a version field
    fields.update(body.model_dump(exclude_none=True, exclude={"rationale", "suggested_prompts"}))
    if "max_run_cost_usd" in body.model_fields_set and body.max_run_cost_usd is None:
        fields["max_run_cost_usd"] = None
    check_tool_config(fields.get("http_tools"), fields.get("mcp_servers"), fields.get("callable_agents"), slug)
    fields["approval_policy"] = checked_policy(fields.get("approval_policy"), fields.get("http_tools")) or {}
    if body.suggested_prompts is not None:
        db.update_agent(agent["id"], suggested_prompts=[p.model_dump() for p in body.suggested_prompts])
    v = db.create_version(agent["id"], **fields, parent_id=prod["id"], created_by="human",
                          rationale=body.rationale or "")
    meta = lesson_provenance(prod["lessons"], prod.get("lesson_meta"), v["lessons"], [], v["n"], origin="human")
    if meta:
        db.set_lesson_meta(v["id"], meta)
        v = db.get_version(v["id"])
    return version_view(v)  # type: ignore[arg-type]


@api.get("/agents/{slug}/versions/{n}")
async def get_version(slug: str, n: str, request: Request) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    return version_view(version_or_404(agent, n))


@api.post("/agents/{slug}/promote")
async def promote(slug: str, body: Promote, request: Request) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    v = version_or_404(agent, body.version)
    return agent_view(db.set_prod_version(agent["id"], v["id"], "human"))  # type: ignore[arg-type]


@api.put("/agents/{slug}/secrets")
async def put_secrets(slug: str, request: Request, body: dict[str, str | None] = Body(...)) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    return {"names": db.set_secrets(agent["id"], body)}


@api.get("/agents/{slug}/webhook-secret")
async def get_webhook_secret(slug: str, request: Request) -> dict:
    """Builder only: the secret that signs this agent's callback_url deliveries (X-Forge-Signature-256)."""
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    return {"secret": db.webhook_secret(agent["id"]), "header": runs.WEBHOOK_SIGNATURE}


@api.post("/agents/{slug}/webhook-secret/rotate")
async def rotate_webhook_secret(slug: str, request: Request) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    return {"secret": db.webhook_secret(agent["id"], rotate=True), "header": runs.WEBHOOK_SIGNATURE}


@api.post("/agents/{slug}/rotate-key")
async def rotate_key(slug: str, request: Request) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    return {"api_key": db.rotate_api_key(agent["id"])}


class ToolTest(BaseModel):
    tool: HttpTool
    args: dict[str, Any] = Field(default_factory=dict)
    network: bool | None = None  # the form's current network setting; defaults to the production version's


class McpList(BaseModel):
    server: McpServer
    network: bool | None = None
    seed_files: dict[str, str] | None = None  # stdio: workspace files to start with (default: production's)


def _secret_values(secrets: dict[str, str]) -> list[str]:
    return sorted({v for v in secrets.values() if v}, key=len, reverse=True)


@api.post("/agents/{slug}/tools/test")
async def test_http_tool(slug: str, body: ToolTest, request: Request) -> dict:
    """Runs an HTTP tool once with sample arguments, exactly as a run would (network policy, SSRF, secrets)."""
    from forge.loop import redact
    from forge.tools.http import run_http_tool

    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    check_tool_config([body.tool], [], [], slug)
    network = body.network if body.network is not None else bool(version_or_404(agent, "prod")["network"])
    secrets = db.get_secrets(agent["id"])
    started = time.monotonic()
    out = await run_http_tool(body.tool, body.args, secrets=secrets, network=network,
                              deadline=time.monotonic() + 130)
    return {"output": redact(out.output, _secret_values(secrets)), "is_error": out.is_error,
            "status": out.extra.get("http_status"), "duration_ms": int((time.monotonic() - started) * 1000)}


@api.post("/agents/{slug}/mcp/list")
async def list_mcp_tools(slug: str, body: McpList, request: Request) -> dict:
    """Connects to an MCP server (stdio servers start in a fresh sandbox) and lists the tools the agent gets."""
    import os

    from forge.executors import make_executor
    from forge.loop import redact
    from forge.tools.mcp import describe_error, list_server_tools

    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    check_tool_config([], [body.server], [], slug)
    prod = version_or_404(agent, "prod")
    network = body.network if body.network is not None else bool(prod["network"])
    secrets = db.get_secrets(agent["id"])
    executor = None
    try:
        if body.server.transport == "stdio":
            executor = make_executor(os.environ.get("FORGE_EXECUTOR") or "docker",  # type: ignore[arg-type]
                                     network=network)
            seeds = body.seed_files if body.seed_files is not None else (prod.get("seed_files") or {})
            await executor.start({k: v.encode() for k, v in seeds.items()}, dict(secrets))
        tools = await list_server_tools(body.server, secrets=secrets, executor=executor, network=network)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, redact(f"could not list tools: {describe_error(exc)}",
                                        _secret_values(secrets))) from None
    finally:
        if executor is not None:
            await executor.stop()
    return {"tools": tools}


@api.get("/templates")
async def templates() -> list[dict]:
    return TEMPLATES


# ---------------------------------------------------------------- uploads


def safe_path(name: str) -> str | None:
    p = PurePosixPath(name.replace("\\", "/").lstrip("/"))
    parts = [x for x in p.parts if x not in ("", ".")]
    if not parts or ".." in parts or ".git" in parts:
        return None
    return "/".join(parts)


def extract_tarball(data: bytes) -> dict[str, bytes]:
    """Text files from a .tar.gz, at most 1MB each; a single shared top-level directory is stripped."""
    out: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for m in tar:
                if len(out) >= 10_000:
                    break
                if not m.isfile() or m.size > MAX_SEED_FILE or not (path := safe_path(m.name)):
                    continue
                f = tar.extractfile(m)
                content = f.read() if f else b""
                if b"\0" in content[:8192]:
                    continue
                out[path] = content
    except tarfile.TarError as exc:
        raise HTTPException(400, f"invalid tarball: {exc}") from exc
    tops = {p.split("/", 1)[0] for p in out}
    if len(tops) == 1 and all("/" in p for p in out):
        out = {p.split("/", 1)[1]: c for p, c in out.items()}
    return out


async def checked_callback_url(url: Any) -> str | None:
    """400 unless `url` is an http(s) URL whose host resolves only to public addresses (the same SSRF check as
    HTTP tools; FORGE_ALLOW_PRIVATE_HTTP=1 allows private targets). Deliveries re-check on every attempt."""
    if url is None:
        return None
    if not isinstance(url, str) or len(url) > 2000:
        raise HTTPException(400, "callback_url must be a URL string")
    from forge.tools.http import ToolError, check_destination

    try:
        await check_destination(url.strip())
    except ToolError as exc:
        raise HTTPException(400, f"callback_url not allowed: {exc}") from exc
    return url.strip()


async def parse_run_request(request: Request) -> tuple[dict[str, Any], dict[str, bytes]]:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data") or ctype.startswith("application/x-www-form-urlencoded"):
        form = await request.form()
        fields = {k: form.get(k) for k in ("input", "version", "wait", "callback_url") if form.get(k) is not None}
        fields["wait"] = str(fields.get("wait", "")).lower() in ("1", "true", "yes", "on")
        uploads = [f for f in form.getlist("files") if hasattr(f, "read")]
        seeds: dict[str, bytes] = {}
        if len(uploads) == 1 and (uploads[0].filename or "").endswith((".tar.gz", ".tgz")):
            seeds = extract_tarball(await uploads[0].read())
        else:
            for f in uploads:
                if path := safe_path(f.filename or ""):
                    seeds[path] = await f.read()
        return fields, seeds
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "body must be JSON or multipart form data") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    seed_files = body.get("seed_files") or {}
    if not isinstance(seed_files, dict):
        raise HTTPException(400, "seed_files must be an object")
    seeds = {p: str(c).encode() for k, c in seed_files.items() if (p := safe_path(k))}
    return body, seeds


# ---------------------------------------------------------------- runtime


@api.post("/agents/{slug}/runs")
async def create_run(slug: str, request: Request) -> dict:
    agent = agent_or_404(slug)
    source = authorize(request, agent)
    fields, seeds = await parse_run_request(request)
    text = fields.get("input")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(400, "input is required")
    version = version_or_404(agent, fields.get("version") or "prod")
    callback_url = await checked_callback_url(fields.get("callback_url") or None)
    mgr = runs.get_manager()
    run_id = await mgr.start_run(agent, version, text, source, seed_overrides=seeds, callback_url=callback_url)
    texts = {}
    for p, c in seeds.items():
        try:
            texts[p] = c.decode()
        except UnicodeDecodeError:
            pass
    if texts:
        d = db.run_dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "uploads.json").write_text(json.dumps(texts))
    if fields.get("wait") is True:
        await mgr.wait(run_id)
    return run_view(db.get_run(run_id), version_n=version["n"])  # type: ignore[arg-type]


@api.get("/agents/{slug}/runs")
async def list_runs(slug: str, request: Request, response: Response,
                    filters: dict = Depends(observability.run_filters)) -> list[dict]:
    agent = agent_or_404(slug)
    if authorize(request, agent) == "share":
        filters["source"] = "share"  # share-link callers only ever see share runs
    rows, total = await asyncio.to_thread(lambda: observability.search_runs(agent_id=agent["id"], **filters))
    response.headers["X-Total-Count"] = str(total)
    return observability.run_list_view(rows)


@api.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict:
    r, _, _ = run_and_agent(run_id, request)
    return run_view(r)


@api.get("/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request, after: int = -1) -> EventSourceResponse:
    run_and_agent(run_id, request)
    last_id = request.headers.get("last-event-id")
    if last_id and last_id.lstrip("-").isdigit():
        after = int(last_id)

    async def gen() -> AsyncIterator[dict]:
        last = after
        while True:
            evs = db.list_events(run_id, last)
            for e in evs:
                last = e["seq"]
                yield {"event": e["type"], "id": str(e["seq"]), "data": json.dumps(event_view(e))}
                if e["type"] == "run_finished":
                    yield {"event": "end", "data": "{}"}
                    return
            if not evs:
                r = db.get_run(run_id)
                if r is None or r["status"] in TERMINAL:  # finished and nothing left after `last`
                    yield {"event": "end", "data": "{}"}
                    return
                await asyncio.sleep(0.25)

    return EventSourceResponse(gen(), ping=15)


@api.get("/runs/{run_id}/events.json")
async def events_json(run_id: str, request: Request, after: int = -1) -> list[dict]:
    run_and_agent(run_id, request)
    return [event_view(e) for e in db.list_events(run_id, after)]


@api.get("/runs/{run_id}/artifacts")
async def artifacts(run_id: str, request: Request) -> FileResponse:
    r, _, _ = run_and_agent(run_id, request)
    if not r["artifact_path"] or not Path(r["artifact_path"]).exists():
        raise HTTPException(404, "no artifacts for this run")
    return FileResponse(r["artifact_path"], media_type="application/gzip", filename=f"{run_id}-workspace.tar.gz")


@api.get("/runs/{run_id}/diff")
async def diff(run_id: str, request: Request) -> PlainTextResponse:
    r, _, _ = run_and_agent(run_id, request)
    if not r["diff_path"] or not Path(r["diff_path"]).exists():
        raise HTTPException(404, "no diff for this run")
    return PlainTextResponse(Path(r["diff_path"]).read_text(errors="replace"))


def _workspace(run_id: str, request: Request) -> tuple[dict, files.Archive]:
    r, _, _ = run_and_agent(run_id, request)  # share tokens: share runs only (404 otherwise)
    if not r["artifact_path"] or not Path(r["artifact_path"]).exists():
        raise HTTPException(404, "no workspace for this run")
    try:
        return r, files.open_archive(r["artifact_path"])
    except files.FileError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@api.get("/runs/{run_id}/files")
async def list_files(run_id: str, request: Request, changed: bool = False) -> list[dict]:
    r, archive = _workspace(run_id, request)
    entries = await asyncio.to_thread(archive.entries)
    out = [{"path": e.path, "size": e.size, "is_text": e.is_text} for e in entries]
    if changed:
        diff_text = Path(r["diff_path"]).read_text(errors="replace") if r["diff_path"] and Path(
            r["diff_path"]).exists() else ""
        kinds = files.changed_files(diff_text)
        for item in out:
            kind = kinds.get(item["path"])
            item["changed"] = kind is not None
            if kind:
                item["change"] = kind
    return out


@api.get("/runs/{run_id}/files/{path:path}")
async def read_file(run_id: str, path: str, request: Request) -> PlainTextResponse:
    _, archive = _workspace(run_id, request)
    try:
        data = await asyncio.to_thread(archive.read, path)
    except files.FileError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return PlainTextResponse(data.decode("utf-8", errors="replace"),
                             headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


class FeedbackIn(BaseModel):
    rating: Literal[1, -1]
    comment: str = ""


@api.post("/runs/{run_id}/feedback")
async def feedback(run_id: str, body: FeedbackIn, request: Request) -> dict:
    r, agent, source = run_and_agent(run_id, request)
    fb = db.create_feedback(run_id, body.rating, body.comment, "local" if r["source"] == "local" else source)
    if body.rating < 0:
        evals.maybe_auto_improve(agent)
    return feedback_view(fb)


# ---------------------------------------------------------------- local runner


class LocalRunIn(BaseModel):
    agent_slug: str
    version_n: int
    input: str
    source: Literal["local"] = "local"


@api.post("/runs")
async def create_local_run(body: LocalRunIn, request: Request) -> dict:
    agent = agent_or_404(body.agent_slug)
    authorize(request, agent, allow_share=False)
    v = version_or_404(agent, body.version_n)
    r = db.create_run(agent["id"], v["id"], "local", body.input, status="running")
    obslog.lifecycle("started", r, agent["slug"])
    return run_view(r, version_n=v["n"])


@api.post("/runs/{run_id}/events")
async def upload_events(run_id: str, request: Request, body: list[dict[str, Any]] = Body(...)) -> dict:
    r, agent, _ = run_and_agent(run_id, request, allow_share=False)
    if r["source"] != "local":
        raise HTTPException(409, "events can only be uploaded for local runs")
    for raw in body:
        e = Event(**{**raw, "run_id": run_id})
        db.append_event(run_id, e.seq, e.ts, e.type, e.payload)
        if e.type == "run_finished":
            p = e.payload
            status = p.get("status") if p.get("status") in TERMINAL else "failed"
            db.update_run(run_id, status=status, output=str(p.get("output") or ""), turns=int(p.get("turns") or 0),
                          tokens_in=int(p.get("tokens_in") or 0), tokens_out=int(p.get("tokens_out") or 0),
                          cache_read_tokens=int(p.get("cache_read_tokens") or 0),
                          cache_write_tokens=int(p.get("cache_write_tokens") or 0), ended_at=e.ts or time.time())
            if (fin := db.get_run(run_id)) is not None:
                obslog.finished(fin, agent["slug"], (db.get_version(fin["version_id"]) or {}).get("model"))
        elif e.type == "error":
            db.update_run(run_id, error=str(e.payload.get("message") or ""))
    return {"ok": True}


# ---------------------------------------------------------------- improvement


class SaveAsEval(BaseModel):
    name: str | None = None
    checker_type: Literal["shell", "judge"] | None = None
    checker: str | None = None
    split: Literal["train", "holdout"] = "train"


class EvalRunIn(BaseModel):
    version: int | None = None


@api.get("/agents/{slug}/evals")
async def list_evals(slug: str, request: Request) -> list[dict]:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    return [eval_case_view(c) for c in db.list_eval_cases(agent["id"])]


@api.post("/agents/{slug}/evals")
async def create_eval(slug: str, body: EvalCaseIn, request: Request) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    c = db.create_eval_case(agent["id"], body.name, body.input, body.checker_type, body.checker, body.split,
                            body.seed_files)
    return eval_case_view(c)


@api.delete("/evals/{case_id}")
async def delete_eval(case_id: str, request: Request) -> dict:
    c = db.get_eval_case(case_id)
    if c is None:
        raise HTTPException(404, "eval case not found")
    authorize(request, db.get_agent(c["agent_id"]), allow_share=False)  # type: ignore[arg-type]
    db.delete_eval_case(case_id)
    return {"ok": True}


def _default_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "case"


@api.post("/runs/{run_id}/save-as-eval")
async def save_as_eval(run_id: str, request: Request, body: SaveAsEval = Body(default_factory=SaveAsEval)) -> dict:
    r, agent, _ = run_and_agent(run_id, request, allow_share=False)
    checker_type = body.checker_type or "judge"
    checker = body.checker
    if not checker:
        if checker_type == "shell":
            raise HTTPException(400, "checker is required for shell checkers")
        notes = [f["comment"] for f in db.list_feedback(run_id) if f["comment"]]
        checker = "The response fully and correctly accomplishes the task."
        if notes:
            checker += " A reviewer said about a previous attempt: " + " | ".join(notes) + \
                " The response must address this feedback."
    uploads = db.run_dir(run_id) / "uploads.json"
    seed_files = json.loads(uploads.read_text()) if uploads.exists() else {}
    c = db.create_eval_case(agent["id"], body.name or _default_name(r["input"]), r["input"], checker_type, checker,
                            body.split, seed_files)
    return eval_case_view(c)


@api.post("/agents/{slug}/evals/run")
async def run_evals(slug: str, request: Request, body: EvalRunIn = Body(default_factory=EvalRunIn)) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    v = version_or_404(agent, body.version if body.version is not None else "prod")
    budget.ensure_budget(agent)  # 402 when the monthly budget is used up
    evals.start_eval(agent, v)
    await asyncio.sleep(0)
    return evals.eval_summary(agent, v)


@api.get("/agents/{slug}/evals/results")
async def eval_results(slug: str, request: Request, version: int | None = None) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    return evals.eval_summary(agent, version_or_404(agent, version if version is not None else "prod"))


@api.post("/agents/{slug}/improve")
async def improve(slug: str, request: Request) -> dict:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    if any(i["status"] == "running" for i in db.list_improvements(agent["id"])):
        raise HTTPException(409, "an improvement is already running for this agent")
    budget.ensure_budget(agent)  # 402 when the monthly budget is used up
    try:
        im = evals.start_improve(agent)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return improvement_view(im)


@api.get("/improvements/{improvement_id}")
async def get_improvement(improvement_id: str, request: Request) -> dict:
    im = db.get_improvement(improvement_id)
    if im is None:
        raise HTTPException(404, "improvement not found")
    authorize(request, db.get_agent(im["agent_id"]), allow_share=False)  # type: ignore[arg-type]
    return improvement_view(im)


@api.get("/agents/{slug}/improvements")
async def list_improvements(slug: str, request: Request) -> list[dict]:
    agent = agent_or_404(slug)
    authorize(request, agent, allow_share=False)
    return [improvement_view(i) for i in db.list_improvements(agent["id"])]


app.include_router(api)
app.include_router(observability.api)
app.include_router(observability.ops)
app.include_router(authoring.router)  # /v1/agent-drafts and /v1/agents/{slug}/prune (before the SPA catch-all)
governance_routes.install(app)  # approvals, /v1/agents/{slug}/governance and the 402 handler
app.include_router(triggers_api.router)  # trigger CRUD and /v1/hooks/github|slack/{id}
app.include_router(sessions_api.router)  # playground sessions
app.include_router(assist.router)  # /v1/assist/{kind} and /v1/agents/{slug}/sample-task
app.include_router(examples_api.router)  # /v1/examples (one click example agents)

# ---------------------------------------------------------------- SPA


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str) -> FileResponse:
    if full_path.startswith("v1/") or full_path == "v1" or not (WEB_DIST / "index.html").exists():
        raise HTTPException(404, "not found")
    target = (WEB_DIST / full_path).resolve()
    if full_path and target.is_file() and target.is_relative_to(WEB_DIST):
        return FileResponse(target)
    return FileResponse(WEB_DIST / "index.html")
