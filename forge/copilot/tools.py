"""The assistant's tools: thin wrappers over Forge's own HTTP API (see CONTRACT.md "Assistant").

Every tool has a JSON schema, a one-line description and a risk:
  read   runs as soon as the model asks for it (lookups only)
  write  changes something or spends money; the engine (forge.copilot.engine) asks the user first and runs it only
         after an explicit approval. A write tool's `describe` builds the one-line confirmation summary (the exact
         effect and any cost) and validates the call, so a bad call fails before the user is asked.

Tools never touch secrets: no tool reads or writes them, the Agent objects they summarize carry no values, and
every tool output is passed through redaction of all stored secret values before the model or the UI sees it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from forge import db, pricing
from forge.copilot import docs, evalgen
from forge.copilot.api_client import ForgeApi, ToolError
from forge.types import ToolSpec

Risk = Literal["read", "write"]
MAX_TOOL_OUTPUT = 6000  # characters of one tool result the model sees
SLUG = {"type": "string", "description": "The agent's slug, e.g. repo-maintainer."}
RUN_ID = {"type": "string", "description": "A run id (r_ followed by 12 hex characters)."}


@dataclass
class ToolResult:
    text: str                                   # what the model sees
    summary: str                                # the one-line `⎿` result shown in the UI
    links: list[dict[str, str]] = field(default_factory=list)   # [{label, href}] relative app paths
    refresh: list[str] = field(default_factory=list)            # data the UI should refetch, e.g. "evals:<slug>"


@dataclass
class ToolContext:
    api: ForgeApi
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CopilotTool:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: Risk
    run: Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]
    describe: Callable[[ToolContext, dict[str, Any]], Awaitable[str]] | None = None  # write tools only

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)


TOOLS: dict[str, CopilotTool] = {}


def tool(name: str, description: str, properties: dict[str, Any], required: list[str], risk: Risk,
         describe: Callable[[ToolContext, dict[str, Any]], Awaitable[str]] | None = None):
    def wrap(fn: Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]):
        if risk == "write" and describe is None:
            raise ValueError(f"write tool {name} needs a confirmation summary")
        TOOLS[name] = CopilotTool(name, description, {"type": "object", "properties": properties,
                                                      "required": required, "additionalProperties": False},
                                  risk, fn, describe)
        return fn
    return wrap


def specs() -> list[ToolSpec]:
    return [t.spec() for t in TOOLS.values()]


# ---------------------------------------------------------------- helpers


def clip(text: Any, n: int = 200) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."


def cap(text: str, n: int = MAX_TOOL_OUTPUT) -> str:
    return text if len(text) <= n else text[: n - 60] + f"\n[... {len(text) - n + 60} more chars cut ...]"


def usd(x: float | None) -> str:
    if x is None:
        return "unknown"
    return f"${x:.2f}" if x == 0 or x >= 0.01 else f"${x:.4f}"


def secret_values() -> list[str]:
    """Every stored secret value (all agents), longest first, for redaction of tool outputs."""
    with db.connect() as c:
        rows = c.execute("SELECT value FROM secrets").fetchall()
    return sorted({r[0] for r in rows if r[0] and len(r[0]) >= 4}, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "[REDACTED]")
    return text


def redact_obj(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {k: redact_obj(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_obj(v, secrets) for v in value]
    return value


def agent_row(slug: str) -> dict:
    a = db.get_agent_by_slug(slug) if isinstance(slug, str) else None
    if a is None:
        raise ToolError(f"agent {slug!r} not found; call list_agents for the slugs")
    return a


def prod_n(agent: dict) -> int | None:
    v = db.get_version(agent["prod_version_id"])
    return v["n"] if v else None


def avg_run_cost(agent_id: str, limit: int = 20) -> float | None:
    """Mean stored cost of the agent's last finished runs (None when there are none with a known price)."""
    with db.connect() as c:
        rows = c.execute("SELECT cost_usd FROM runs WHERE agent_id = ? AND ended_at IS NOT NULL AND cost_usd IS NOT "
                         "NULL ORDER BY created_at DESC LIMIT ?", (agent_id, limit)).fetchall()
    vals = [r[0] for r in rows]
    return sum(vals) / len(vals) if vals else None


def cost_phrase(agent: dict, runs: int) -> str:
    avg = avg_run_cost(agent["id"])
    if avg is None:
        return "cost unknown until the agent has finished runs"
    return f"about {usd(avg * runs)} at the recent average of {usd(avg)} per run"


def grader_cost(tokens_in: int = 12000, tokens_out: int = 6000) -> str:
    """Price range of one or two grader calls (a reply that fails validation is retried once). The defaults
    match a measured eval case generation on the demo agent: about 7k input and 6k output tokens per call,
    thinking included."""
    from forge.server.evals import grader_model

    c = pricing.cost(grader_model(), tokens_in, tokens_out)
    return f"about {usd(c)} to {usd(2 * c)}" if c is not None else "cost unknown"


def eval_runs_needed(agent: dict, version_n: int | None) -> tuple[int, int, int]:
    """(cases, k, sandbox runs still missing for that version) from the cached per-case summaries."""
    from forge.server.evals import eval_repeats, results_map

    cases = db.list_eval_cases(agent["id"])
    k = eval_repeats(agent)
    v = db.get_version_n(agent["id"], version_n if version_n is not None else "prod")
    cached = results_map(v["id"]) if v else {}
    missing = 0
    for c in cases:
        done = len(db.list_eval_run_results(v["id"], c["id"])) if v else 0
        missing += 0 if cached.get(c["id"], {}).get("runs") == k else max(0, k - done)
    return len(cases), k, missing


def agent_path(slug: str, tab: str | None = None) -> str:
    return f"/agents/{slug}/{tab}" if tab else f"/agents/{slug}"


EVENT_SKIP = {"run_started"}


def condensed_trace(events: list[dict], *, max_lines: int = 30) -> list[str]:
    """One line per step: narration, tool calls with their key argument, results (errors in full-ish), errors.
    Long traces keep the first few steps, every error, and the last steps."""
    lines: list[tuple[bool, str]] = []
    for e in events:
        t, p = e["type"], e.get("payload") or {}
        if t in EVENT_SKIP:
            continue
        if t == "assistant":
            line = f"#{e['seq']} assistant: {clip(p.get('text'), 160)}"
        elif t == "tool_call":
            args = p.get("args") or {}
            key = args.get("cmd") or args.get("path") or json.dumps(args)[:120]
            line = f"#{e['seq']} call {p.get('name')}: {clip(key, 140)}"
        elif t == "tool_result":
            tag = "ERROR " if p.get("is_error") else ""
            line = f"#{e['seq']} result {tag}{p.get('name')}: {clip(p.get('output'), 240 if tag else 100)}"
        elif t == "error":
            line = f"#{e['seq']} error: {clip(p.get('message'), 300)}"
        elif t == "run_finished":
            line = f"#{e['seq']} finished: {p.get('status')}"
        elif t in ("approval_requested", "approval_resolved", "budget_warning"):
            line = f"#{e['seq']} {t}: {clip(json.dumps(p), 160)}"
        else:
            continue
        important = t == "error" or (t == "tool_result" and bool(p.get("is_error")))
        lines.append((important, line))
    if len(lines) <= max_lines:
        return [ln for _, ln in lines]
    head, tail = lines[:5], lines[-15:]
    middle = [x for x in lines[5:-15] if x[0]][: max_lines - 20]
    skipped = len(lines) - len(head) - len(tail) - len(middle)
    return [ln for _, ln in head] + [ln for _, ln in middle] + [f"[... {skipped} steps omitted ...]"] + \
        [ln for _, ln in tail]


def need(args: dict, key: str) -> Any:
    v = args.get(key)
    if v is None or v == "":
        raise ToolError(f"missing {key}")
    return v


# ---------------------------------------------------------------- read tools


@tool("list_agents", "List every agent with its slug, name, production version and budget state.", {}, [], "read")
async def list_agents(ctx: ToolContext, args: dict) -> ToolResult:
    agents = await ctx.api.get("/agents")
    lines = [f"- {a['slug']} ({a['name']}): prod v{a['prod_version']}, budget {a.get('budget_state', 'none')}"
             + (f", {a['pending_approvals']} pending approvals" if a.get("pending_approvals") else "")
             + (f". {clip(a['description'], 120)}" if a.get("description") else "") for a in agents]
    return ToolResult("\n".join(lines) or "No agents yet.", f"{len(agents)} agents", [{"label": "Agents", "href": "/"}])


@tool("get_agent", "Show an agent's configuration summary, its versions and which one is in production.",
      {"slug": SLUG}, ["slug"], "read")
async def get_agent(ctx: ToolContext, args: dict) -> ToolResult:
    slug = need(args, "slug")
    a = await ctx.api.get(f"/agents/{slug}")
    versions = a.get("versions") or []
    prod = next((v for v in versions if v["n"] == a.get("prod_version")), versions[-1] if versions else {})
    out = [f"agent: {a['slug']} ({a['name']})", f"description: {a.get('description') or '(none)'}",
           f"production version: v{a.get('prod_version')} of {len(versions)}",
           f"auto_promote: {a.get('auto_promote')}, eval_repeats (k): {a.get('eval_repeats')}",
           f"monthly budget: {usd(a['monthly_budget_usd']) if a.get('monthly_budget_usd') is not None else 'none'}"
           f", month to date {usd(a.get('month_to_date_usd'))} ({a.get('budget_state')})"]
    if prod:
        pol = (prod.get("approval_policy") or {}).get("rules") or []
        out += ["", f"prod v{prod['n']} config:", f"model: {prod['model']}", f"tools: {', '.join(prod['tools'])}",
                f"network: {'on' if prod['network'] else 'off'}",
                f"http tools: {', '.join(t['name'] for t in prod.get('http_tools') or []) or '(none)'}",
                f"mcp servers: {', '.join(m['name'] for m in prod.get('mcp_servers') or []) or '(none)'}",
                f"callable agents: {', '.join(prod.get('callable_agents') or []) or '(none)'}",
                f"seed files: {len(prod.get('seed_files') or {})}",
                f"approval rules: {json.dumps(pol) if pol else '(none)'}",
                f"per-run cap: {usd(prod['max_run_cost_usd']) if prod.get('max_run_cost_usd') is not None else 'none'}",
                f"lessons ({len(prod['lessons'])}):", *[f"  - {clip(x, 300)}" for x in prod["lessons"]],
                "system prompt:", cap(prod["system_prompt"], 2500)]
    out += ["", "versions:"] + [f"- v{v['n']} {v['model']} by {v['created_by']}"
                                f"{' (prod)' if v['n'] == a.get('prod_version') else ''}: {clip(v['rationale'], 120)}"
                                for v in versions[-12:]]
    if a.get("suggested_prompts"):
        out.append("suggested prompts: " + "; ".join(p["title"] for p in a["suggested_prompts"]))
    return ToolResult("\n".join(out), f"{a['name']}, prod v{a.get('prod_version')}, {len(versions)} versions",
                      [{"label": a["name"], "href": agent_path(slug, "configure")}])


@tool("list_runs", "List recent runs of one agent (or all agents) with optional filters, newest first.",
      {"slug": {"type": "string", "description": "Agent slug; omit for all agents."},
       "status": {"type": "string", "description": "Comma separated statuses, e.g. failed,timeout."},
       "version": {"type": "string", "description": "Comma separated version numbers."},
       "source": {"type": "string", "description": "Comma separated sources: api, ui, eval, playground, schedule..."},
       "q": {"type": "string", "description": "Case-insensitive text in the input or output."},
       "limit": {"type": "integer", "description": "1 to 25, default 10."}}, [], "read")
async def list_runs(ctx: ToolContext, args: dict) -> ToolResult:
    limit = max(1, min(int(args.get("limit") or 10), 25))
    params = {k: args.get(k) for k in ("status", "version", "source", "q")} | {"limit": limit}
    slug = args.get("slug")
    if slug:
        runs, total = await ctx.api.call("GET", f"/agents/{slug}/runs", params=params, with_total=True)
    else:
        runs, total = await ctx.api.call("GET", "/runs", params=params, with_total=True)
    lines = [f"- {r['id']} {r.get('agent_slug') or slug or ''} v{r['version_n']} {r['status']} ({r['source']}) "
             f"cost {usd(r.get('cost_usd'))}: {clip(r['input'], 90)}"
             + (f" | error: {clip(r['error'], 120)}" if r.get("error") else "") for r in runs]
    total = total if total is not None else len(runs)
    href = agent_path(slug, "runs") if slug else "/dashboard"
    return ToolResult("\n".join(lines) or "No runs match.", f"{len(runs)} of {total} runs",
                      [{"label": "Runs", "href": href}])


async def _run_detail(ctx: ToolContext, run_id: str, *, max_lines: int) -> tuple[dict, list[str], list[dict]]:
    run = await ctx.api.get(f"/runs/{run_id}")
    events = await ctx.api.get(f"/runs/{run_id}/events.json")
    agent = db.get_agent(run["agent_id"]) or {}
    out = [f"run: {run['id']} of {agent.get('slug', '?')} v{run['version_n']} (source {run['source']})",
           f"status: {run['status']}", f"error: {run.get('error') or '(none)'}",
           f"turns: {run['turns']}, tokens in/out: {run['tokens_in']}/{run['tokens_out']}, cost: {usd(run.get('cost_usd'))}",
           f"duration: {run['duration_ms'] / 1000:.1f}s" if run.get("duration_ms") else "duration: (not finished)",
           f"input: {cap(run['input'], 600)}", f"output: {cap(run.get('output') or '(none)', 1500)}"]
    if run.get("eval_case_id"):
        case = db.get_eval_case(run["eval_case_id"])
        with db.connect() as c:
            row = c.execute("SELECT passed, reason FROM eval_run_results WHERE run_id = ?", (run_id,)).fetchone()
        out.append(f"eval case: {case['name'] if case else run['eval_case_id']}"
                   + (f", verdict: {'PASS' if row[0] else 'FAIL'}: {clip(row[1], 400)}" if row else ""))
    if run.get("feedback"):
        out.append("feedback: " + "; ".join(f"{'+1' if f['rating'] > 0 else '-1'} {clip(f['comment'], 120)}"
                                              for f in run["feedback"]))
    out += ["", "trace:"] + condensed_trace(events, max_lines=max_lines)
    return run, out, events


@tool("get_run", "Show one run: status, input, output, error, cost and a condensed trace of its steps.",
      {"run_id": RUN_ID}, ["run_id"], "read")
async def get_run(ctx: ToolContext, args: dict) -> ToolResult:
    run, out, _ = await _run_detail(ctx, need(args, "run_id"), max_lines=30)
    return ToolResult("\n".join(out), f"{run['status']} · {run['turns']} turns · {usd(run.get('cost_usd'))}",
                      [{"label": run["id"], "href": f"/runs/{run['id']}"}])


@tool("explain_failure", "Gather what is needed to explain why a run failed: its error, the failing steps and "
      "the eval verdict when it was an eval run.", {"run_id": RUN_ID}, ["run_id"], "read")
async def explain_failure(ctx: ToolContext, args: dict) -> ToolResult:
    run, out, events = await _run_detail(ctx, need(args, "run_id"), max_lines=40)
    errors = [e for e in events if e["type"] == "error" or (e["type"] == "tool_result" and e["payload"].get("is_error"))]
    out.append(f"\n{len(errors)} error steps")
    return ToolResult("\n".join(out), f"{run['status']}: {clip(run.get('error') or 'no error message', 80)}",
                      [{"label": run["id"], "href": f"/runs/{run['id']}"}])


@tool("list_eval_cases", "List an agent's eval cases with their split, checker type and input.",
      {"slug": SLUG}, ["slug"], "read")
async def list_eval_cases(ctx: ToolContext, args: dict) -> ToolResult:
    slug = need(args, "slug")
    cases = await ctx.api.get(f"/agents/{slug}/evals")
    lines = [f"- {c['id']} {c['name']} [{c['split']}, {c['checker_type']}]: {clip(c['input'], 140)} | checker: "
             f"{clip(c['checker'], 100)}" for c in cases]
    n_train = sum(c["split"] == "train" for c in cases)
    return ToolResult("\n".join(lines) or "No eval cases yet.",
                      f"{len(cases)} cases ({n_train} train, {len(cases) - n_train} holdout)",
                      [{"label": "Evaluations", "href": agent_path(slug, "evals")}])


@tool("get_eval_results", "Show a version's eval results per case (majority of k runs) and the train and held-out "
      "pass counts. Defaults to the production version.",
      {"slug": SLUG, "version": {"type": "integer", "description": "Version number; omit for production."}},
      ["slug"], "read")
async def get_eval_results(ctx: ToolContext, args: dict) -> ToolResult:
    slug = need(args, "slug")
    res = await ctx.api.get(f"/agents/{slug}/evals/results", version=args.get("version"))
    cases = {c["id"]: c for c in await ctx.api.get(f"/agents/{slug}/evals")}
    by_case = {r["case_id"]: r for r in res["results"]}
    lines = [f"v{res['version']}: train {res['train']['passed']}/{res['train']['total']}, held-out "
             f"{res['holdout']['passed']}/{res['holdout']['total']} (k={res.get('repeats')})"
             + (" (evaluation running)" if res.get("running") else "")]
    for cid, c in cases.items():
        r = by_case.get(cid)
        if r is None:
            lines.append(f"- {c['name']} [{c['split']}]: not evaluated")
        else:
            lines.append(f"- {c['name']} [{c['split']}]: {'PASS' if r['passed'] else 'FAIL'} {r['passes']}/{r['runs']}"
                         f" (run {r['run_id']}){'' if r['passed'] else ': ' + clip(r['reason'], 200)}")
    return ToolResult("\n".join(lines), lines[0], [{"label": "Evaluations", "href": agent_path(slug, "evals")}])


@tool("get_improvements", "List an agent's improvement and prune cycles, or show one cycle in detail.",
      {"slug": SLUG, "improvement_id": {"type": "string", "description": "An im_ id for details; omit to list."}},
      ["slug"], "read")
async def get_improvements(ctx: ToolContext, args: dict) -> ToolResult:
    slug = need(args, "slug")
    link = [{"label": "Evaluations", "href": agent_path(slug, "evals")}]
    if args.get("improvement_id"):
        im = await ctx.api.get(f"/improvements/{args['improvement_id']}")
        out = [f"{im['id']} ({im['kind']}) {im['status']}: v{im['base_version']} -> v{im['candidate_version']}",
               f"base: train {im['base']['train']['passed']}/{im['base']['train']['total']}, held-out "
               f"{im['base']['holdout']['passed']}/{im['base']['holdout']['total']}",
               f"candidate: train {im['candidate']['train']['passed']}/{im['candidate']['train']['total']}, held-out "
               f"{im['candidate']['holdout']['passed']}/{im['candidate']['holdout']['total']}",
               f"error: {im.get('error') or '(none)'}", "lesson edits:"]
        out += [f"- {d['op']}: {clip(d['text'], 240)}" for d in im.get("lessons_diff") or []]
        out += ["cases:"] + [f"- {c['name']} [{c['split']}]: base {'PASS' if c['base'] and c['base']['passed'] else 'FAIL'}"
                             f", candidate {'PASS' if c.get('candidate') and c['candidate']['passed'] else 'FAIL'}"
                             for c in im.get("cases") or []]
        return ToolResult("\n".join(out), f"{im['status']}", link)
    ims = await ctx.api.get(f"/agents/{slug}/improvements")
    lines = [f"- {i['id']} ({i['kind']}) {i['status']}: v{i['base_version']} -> v{i['candidate_version']}"
             + (f", error: {clip(i['error'], 120)}" if i.get("error") else "") for i in ims[:15]]
    return ToolResult("\n".join(lines) or "No improvement cycles yet.", f"{len(ims)} cycles", link)


@tool("get_metrics", "Show health and cost: success rate, latency, spend, top failure reasons. With a slug for one "
      "agent, without for the overview of all agents.",
      {"slug": {"type": "string", "description": "Agent slug; omit for all agents."},
       "days": {"type": "integer", "description": "Period in days, default 14."}}, [], "read")
async def get_metrics(ctx: ToolContext, args: dict) -> ToolResult:
    days = max(1, min(int(args.get("days") or 14), 365))
    slug = args.get("slug")

    def health(h: dict) -> str:
        rate = f"{h['success_rate'] * 100:.0f}%" if h.get("success_rate") is not None else "n/a"
        p95 = f"{h['p95_ms'] / 1000:.1f}s" if h.get("p95_ms") is not None else "n/a"
        return (f"{h['runs']} runs, success {rate}, p95 {p95}, cost {usd(h.get('cost_usd'))} "
                f"({usd(h.get('cost_per_run'))} per run), thumbs up {h.get('thumbs_up', 0)}, down "
                f"{h.get('thumbs_down', 0)}")

    if slug:
        m = await ctx.api.get(f"/agents/{slug}/metrics", days=days)
        out = [f"{slug}, last {days} days: {health(m['summary'])}", f"previous period: {health(m['previous'])}",
               f"total cost in period: {usd(m['cost']['total_usd'])}"]
        if (a := m["cost"].get("assist")) and a.get("calls"):
            out.append(f"assist and assistant calls: {a['calls']} ({usd(a['cost_usd'])})")
        out.append("top failure reasons:")
        out += [f"- {f['label']}: {clip(f['reason'], 140)} ({f['runs']} runs, e.g. {', '.join(f['example_run_ids'][:2])})"
                for f in m["failures"]["all"]["reasons"][:5]] or ["- none"]
        return ToolResult("\n".join(out), health(m["summary"]),
                          [{"label": "Monitoring", "href": agent_path(slug, "monitoring")}])
    o = await ctx.api.get("/metrics/overview", days=days)
    out = [f"All agents, last {days} days: {health(o['summary'])}"]
    out += [f"- {a['slug']}: {health(a['health'])}" for a in o["agents"]]
    if (c := o.get("copilot")) and c.get("calls"):
        out.append(f"assistant (not tied to an agent): {c['calls']} calls, {usd(c['cost_usd'])}")
    out += ["top failure reasons:"] + [f"- {f.get('agent')}: {f['label']}: {clip(f['reason'], 120)}"
                                       for f in o.get("top_failure_reasons", [])[:5]]
    return ToolResult("\n".join(out), health(o["summary"]), [{"label": "Dashboard", "href": "/dashboard"}])


@tool("docs_lookup", "Search Forge's documentation (README, CONTRACT, TESTING, docs/) for how something works.",
      {"query": {"type": "string", "description": "What to look up, in a few words."}}, ["query"], "read")
async def docs_lookup(ctx: ToolContext, args: dict) -> ToolResult:
    text = docs.lookup(str(need(args, "query")))
    n = text.count("\n## ") + (1 if text.startswith("## ") else 0)
    return ToolResult(text, f"{n} sections" if n else "no match")


# ---------------------------------------------------------------- write tools: confirmation summaries


async def _d_create_eval_case(ctx: ToolContext, a: dict) -> str:
    agent_row(need(a, "slug"))
    if a.get("checker_type") not in ("shell", "judge"):
        raise ToolError("checker_type must be shell or judge")
    if a.get("split", "train") not in ("train", "holdout"):
        raise ToolError("split must be train or holdout")
    for k in ("name", "input", "checker"):
        need(a, k)
    return (f"Add {a.get('split', 'train')} eval case \"{a['name']}\" to {a['slug']} ({a['checker_type']} checker: "
            f"{clip(a['checker'], 90)}). Input: {clip(a['input'], 120).rstrip('.')}. Free until evaluated.")


async def _d_delete_eval_case(ctx: ToolContext, a: dict) -> str:
    c = db.get_eval_case(str(need(a, "case_id")))
    if c is None:
        raise ToolError(f"eval case {a['case_id']!r} not found")
    agent = db.get_agent(c["agent_id"]) or {}
    return f"Delete {c['split']} eval case \"{c['name']}\" ({c['id']}) from {agent.get('slug')}. This cannot be undone."


async def _d_generate(ctx: ToolContext, a: dict) -> str:
    from forge.copilot.eval_fallback import parse_count

    agent = agent_row(need(a, "slug"))
    try:
        n = parse_count(a.get("count"))
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    split = f" {a['split']}" if a.get("split") else ""
    return (f"Write {n} new{split} eval cases about \"{clip(need(a, 'topic'), 80)}\" for {agent['slug']} with the "
            f"grader model and add them (one or two model calls, {grader_cost()}).")


async def _d_start_run(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    v = a.get("version") or prod_n(agent)
    if a.get("version") is not None and db.get_version_n(agent["id"], a["version"]) is None:
        raise ToolError(f"version {a['version']} not found")
    what = f"input \"{clip(a['input'], 120)}\"" if a.get("input") else "a task written by the grader model"
    if not a.get("input") and not a.get("generate_task"):
        raise ToolError("give input, or set generate_task to true")
    return f"Start one run of {agent['slug']} v{v} with {what} ({cost_phrase(agent, 1)})."


async def _d_run_evaluation(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    n = a.get("version") or prod_n(agent)
    if db.get_version_n(agent["id"], n) is None:
        raise ToolError(f"version {n} not found")
    cases, k, missing = eval_runs_needed(agent, n)
    if not cases:
        raise ToolError(f"{agent['slug']} has no eval cases; add some first")
    if missing == 0:
        return f"Evaluate {agent['slug']} v{n}: all {cases} cases already have {k} cached runs, so nothing new runs (free)."
    return (f"Evaluate {agent['slug']} v{n} on {cases} cases, k={k}: {missing} new sandbox runs plus judge calls "
            f"({cost_phrase(agent, missing)}).")


async def _d_improve(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    cases, k, missing = eval_runs_needed(agent, None)
    if not cases:
        raise ToolError(f"{agent['slug']} has no eval cases; an improvement needs them")
    total = missing + cases * k
    return (f"Start an improvement cycle for {agent['slug']}: evaluate prod v{prod_n(agent)}, have the grader propose "
            f"lesson edits, then evaluate the candidate version (up to {total} sandbox runs, "
            f"{cost_phrase(agent, total)}, plus grader calls). The candidate is not promoted unless auto promote is on.")


async def _d_promote(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    n = need(a, "version")
    if db.get_version_n(agent["id"], n) is None:
        raise ToolError(f"version {n} not found")
    return f"Make v{n} the production version of {agent['slug']} (currently v{prod_n(agent)}). Free."


VERSION_FIELDS = ("system_prompt", "model", "tools", "network", "lessons")


async def _d_create_version(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    prod = db.get_version(agent["prod_version_id"]) or {}
    changes = []
    for k in VERSION_FIELDS:
        if k not in a or a[k] is None:
            continue
        old, new = prod.get(k), a[k]
        if old == new:
            continue
        if k == "system_prompt":
            changes.append(f"system prompt ({len(old or '')} -> {len(new)} chars)")
        elif k == "lessons":
            added = [x for x in new if x not in (old or [])]
            removed = [x for x in old or [] if x not in new]
            changes.append(f"lessons (+{len(added)} -{len(removed)})")
        elif k == "network":
            changes.append(f"network {'on' if old else 'off'} -> {'on' if new else 'off'}")
        else:
            changes.append(f"{k} {old} -> {new}")
    if not changes:
        raise ToolError("nothing differs from production; pass the fields to change")
    nxt = max((v["n"] for v in db.list_versions(agent["id"])), default=0) + 1
    return (f"Create v{nxt} of {agent['slug']} from prod v{prod.get('n')} changing {'; '.join(changes)}. Not promoted; "
            f"free until it runs.")


def _rule_text(r: dict) -> str:
    pat = f" matching {r['pattern']!r}" if r.get("pattern") else ""
    return f"{r.get('mode', 'ask')} before {r.get('tool')}{pat}"


async def _d_policy(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    rules = a.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ToolError("rules must be a non-empty list")
    prod = db.get_version(agent["prod_version_id"]) or {}
    from forge.policy import http_tool_names, parse_policy

    merged = _merged_policy(prod, a)
    try:
        parse_policy(merged, tools=http_tool_names(prod.get("http_tools")))
    except ValueError as exc:
        raise ToolError(f"invalid approval policy: {exc}") from None
    nxt = max((v["n"] for v in db.list_versions(agent["id"])), default=0) + 1
    verb = "replacing all rules with" if a.get("replace") else "adding"
    return (f"Create v{nxt} of {agent['slug']} {verb}: {'; '.join(_rule_text(r) for r in rules)}. It applies once "
            f"v{nxt} is promoted. Free.")


def _merged_policy(prod: dict, a: dict) -> dict:
    old = prod.get("approval_policy") or {}
    rules = list(a["rules"]) if a.get("replace") else [*(old.get("rules") or []), *a["rules"]]
    out = {"rules": rules}
    if a.get("timeout_seconds") or old.get("timeout_seconds"):
        out["timeout_seconds"] = a.get("timeout_seconds") or old.get("timeout_seconds")
    return out


async def _d_budget(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    if "monthly_budget_usd" not in a and "max_run_cost_usd" not in a:
        raise ToolError("give monthly_budget_usd and/or max_run_cost_usd (null removes one)")
    parts = []
    for k, label in (("monthly_budget_usd", "monthly budget"), ("max_run_cost_usd", "per-run spending cap")):
        if k in a:
            v = a[k]
            if v is not None and (not isinstance(v, (int, float)) or v < 0):
                raise ToolError(f"{k} must be a non-negative number or null")
            parts.append(f"remove the {label}" if v is None else f"set the {label} to {usd(float(v))}")
    note = " (the per-run cap is a version setting: this creates a new version, not promoted)" \
        if "max_run_cost_usd" in a else ""
    return f"For {agent['slug']}: {'; '.join(parts)}{note}."


async def _d_trigger(ctx: ToolContext, a: dict) -> str:
    agent = agent_row(need(a, "slug"))
    from forge.triggers import core

    try:
        core.validate_config("schedule", _schedule_config(a))
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    return (f"Add a schedule to {agent['slug']}: cron \"{a['cron']}\" ({a.get('timezone') or 'UTC'}) runs "
            f"\"{clip(a['input_template'], 100)}\"{'' if a.get('enabled', True) else ' (disabled)'}. Each run costs "
            f"like any run ({cost_phrase(agent, 1)}).")


def _schedule_config(a: dict) -> dict:
    cfg = {"cron": need(a, "cron"), "input_template": need(a, "input_template")}
    if a.get("timezone"):
        cfg["timezone"] = a["timezone"]
    return cfg


async def _d_save_as_eval(ctx: ToolContext, a: dict) -> str:
    run = db.get_run(str(need(a, "run_id")))
    if run is None:
        raise ToolError("run not found")
    agent = db.get_agent(run["agent_id"]) or {}
    kind = a.get("checker_type") or "judge"
    checker = f"{kind} checker: {clip(a['checker'], 80)}" if a.get("checker") else "default judge rubric from feedback"
    return (f"Save run {run['id']} as a {a.get('split', 'train')} eval case of {agent.get('slug')} ({checker}). Input: "
            f"{clip(run['input'], 100)}. Free.")


async def _d_feedback(ctx: ToolContext, a: dict) -> str:
    if a.get("rating") not in (1, -1):
        raise ToolError("rating must be 1 or -1")
    if db.get_run(str(need(a, "run_id"))) is None:
        raise ToolError("run not found")
    return (f"Add {'thumbs up' if a['rating'] == 1 else 'thumbs down'} feedback to run {a['run_id']}"
            + (f": \"{clip(a['comment'], 100)}\"" if a.get("comment") else "") + ". Free.")


# ---------------------------------------------------------------- write tools


CASE_PROPS = {
    "slug": SLUG,
    "name": {"type": "string", "description": "Short unique slug name, e.g. refund-partial-order."},
    "input": {"type": "string", "description": "The task the agent receives, as a real user would write it."},
    "checker_type": {"type": "string", "enum": ["shell", "judge"],
                     "description": "shell: a command that exits 0 on success; judge: a PASS only if rubric."},
    "checker": {"type": "string", "description": "The shell command, or the judge rubric (60 to 1200 chars)."},
    "split": {"type": "string", "enum": ["train", "holdout"], "description": "Default train."},
}


@tool("create_eval_case", "Add one eval case to an agent. To add several, call it several times in the same turn: "
      "the user confirms them together.", CASE_PROPS, ["slug", "name", "input", "checker_type", "checker"], "write",
      _d_create_eval_case)
async def create_eval_case(ctx: ToolContext, a: dict) -> ToolResult:
    body = {k: a[k] for k in ("name", "input", "checker_type", "checker")} | {"split": a.get("split") or "train"}
    c = await ctx.api.post(f"/agents/{a['slug']}/evals", body)
    return ToolResult(f"Created {c['id']} {c['name']} [{c['split']}]", f"created {c['name']} ({c['split']})",
                      [{"label": "Evaluations", "href": agent_path(a["slug"], "evals")}], [f"evals:{a['slug']}"])


@tool("delete_eval_case", "Delete one eval case by id.", {"case_id": {"type": "string", "description": "An ec_ id."}},
      ["case_id"], "write", _d_delete_eval_case)
async def delete_eval_case(ctx: ToolContext, a: dict) -> ToolResult:
    c = db.get_eval_case(a["case_id"])
    slug = (db.get_agent(c["agent_id"]) or {}).get("slug", "") if c else ""
    await ctx.api.call("DELETE", f"/evals/{a['case_id']}")
    return ToolResult(f"Deleted {a['case_id']}", f"deleted {c['name'] if c else a['case_id']}",
                      [{"label": "Evaluations", "href": agent_path(slug, "evals")}], [f"evals:{slug}"])


@tool("generate_eval_cases", "Have the grader model write 1 to 5 eval cases about a topic, grounded in the agent's "
      "files, and add them. Prefer writing cases yourself with create_eval_case when you know what to test.",
      {"slug": SLUG, "topic": {"type": "string", "description": "What the cases should test."},
       "count": {"type": "integer", "description": "1 to 5, default 3."},
       "split": {"type": "string", "enum": ["train", "holdout"], "description": "Omit to let the grader mix."}},
      ["slug", "topic"], "write", _d_generate)
async def generate_eval_cases(ctx: ToolContext, a: dict) -> ToolResult:
    cases = await evalgen.generate(ctx.api, a["slug"], a["topic"], int(a.get("count") or 3), a.get("split"))
    created = []
    for c in cases:
        created.append(await ctx.api.post(f"/agents/{a['slug']}/evals", c))
    text = "\n".join(f"Created {c['id']} {c['name']} [{c['split']}, {c['checker_type']}]: {clip(c['input'], 160)}"
                     for c in created)
    return ToolResult(text, f"created {len(created)} cases: {', '.join(c['name'] for c in created)}",
                      [{"label": "Evaluations", "href": agent_path(a["slug"], "evals")}], [f"evals:{a['slug']}"])


@tool("start_run", "Start one run of an agent with an input (or a task the grader writes). Returns at once; check "
      "it later with get_run.",
      {"slug": SLUG, "input": {"type": "string", "description": "The task text."},
       "version": {"type": "integer", "description": "Version number; omit for production."},
       "generate_task": {"type": "boolean", "description": "true to have the grader write a realistic task."}},
      ["slug"], "write", _d_start_run)
async def start_run(ctx: ToolContext, a: dict) -> ToolResult:
    text = a.get("input")
    if not text:
        t = await ctx.api.post(f"/agents/{a['slug']}/sample-task", {"version": a.get("version")})
        text = t["prompt"]
    body = {"input": text} | ({"version": a["version"]} if a.get("version") else {})
    r = await ctx.api.post(f"/agents/{a['slug']}/runs", body)
    return ToolResult(f"Started run {r['id']} ({r['status']}) of v{r['version_n']} with input: {clip(text, 300)}",
                      f"started {r['id']}", [{"label": r["id"], "href": f"/runs/{r['id']}"}], [f"runs:{a['slug']}"])


@tool("run_evaluation", "Evaluate a version (default production) on every eval case, k runs each; cached results "
      "are reused. Returns at once; poll get_eval_results.",
      {"slug": SLUG, "version": {"type": "integer", "description": "Version number; omit for production."}},
      ["slug"], "write", _d_run_evaluation)
async def run_evaluation(ctx: ToolContext, a: dict) -> ToolResult:
    body = {"version": a["version"]} if a.get("version") else {}
    r = await ctx.api.post(f"/agents/{a['slug']}/evals/run", body)
    return ToolResult(f"Evaluation of v{r['version']} {'running' if r.get('running') else 'finished (cached)'}; "
                      f"train {r['train']['passed']}/{r['train']['total']}, held-out {r['holdout']['passed']}/"
                      f"{r['holdout']['total']} so far.", f"evaluating v{r['version']}",
                      [{"label": "Evaluations", "href": agent_path(a["slug"], "evals")}], [f"evals:{a['slug']}"])


@tool("start_improvement", "Start an improvement cycle: evaluate production, propose lesson edits from failures and "
      "negative feedback, evaluate the candidate and apply the promotion rule. Returns at once; poll get_improvements.",
      {"slug": SLUG}, ["slug"], "write", _d_improve)
async def start_improvement(ctx: ToolContext, a: dict) -> ToolResult:
    im = await ctx.api.post(f"/agents/{a['slug']}/improve", {})
    return ToolResult(f"Started improvement {im['id']} ({im['status']}) from v{im['base_version']}.",
                      f"started {im['id']}", [{"label": "Evaluations", "href": agent_path(a["slug"], "evals")}],
                      [f"evals:{a['slug']}", f"agent:{a['slug']}"])


@tool("promote_version", "Make a version the agent's production version.",
      {"slug": SLUG, "version": {"type": "integer", "description": "The version number to promote."}},
      ["slug", "version"], "write", _d_promote)
async def promote_version(ctx: ToolContext, a: dict) -> ToolResult:
    ag = await ctx.api.post(f"/agents/{a['slug']}/promote", {"version": a["version"]})
    return ToolResult(f"{a['slug']} production is now v{ag['prod_version']}.", f"prod is v{ag['prod_version']}",
                      [{"label": "Versions", "href": agent_path(a["slug"], "configure")}], [f"agent:{a['slug']}"])


@tool("create_version", "Create a new (unpromoted) version from production with some fields changed: system prompt, "
      "model, tools, network or lessons (lessons replaces the whole list).",
      {"slug": SLUG, "system_prompt": {"type": "string"}, "model": {"type": "string", "description":
       "anthropic:<id> or openai_compat:<base_url>|<model>"},
       "tools": {"type": "array", "items": {"type": "string", "enum": ["bash", "read_file", "write_file", "edit_file"]}},
       "network": {"type": "boolean"}, "lessons": {"type": "array", "items": {"type": "string"}},
       "rationale": {"type": "string", "description": "Why, shown in the version list."}},
      ["slug"], "write", _d_create_version)
async def create_version(ctx: ToolContext, a: dict) -> ToolResult:
    body = {k: a[k] for k in (*VERSION_FIELDS, "rationale") if a.get(k) is not None}
    body.setdefault("rationale", "Created by the Forge assistant")
    v = await ctx.api.post(f"/agents/{a['slug']}/versions", body)
    return ToolResult(f"Created v{v['n']} (not promoted).", f"created v{v['n']}",
                      [{"label": f"v{v['n']}", "href": agent_path(a["slug"], "configure")}], [f"agent:{a['slug']}"])


RULE = {"type": "object", "properties": {
    "tool": {"type": "string", "description": "bash, read_file, write_file, edit_file, *, an HTTP tool name, "
             "mcp__<server>__<tool>, agent__<slug>, or a prefix ending in *."},
    "pattern": {"type": "string", "description": "bash: regex searched in the command; file tools: a path glob."},
    "mode": {"type": "string", "enum": ["ask", "deny"]},
    "label": {"type": "string"}}, "required": ["tool", "mode"], "additionalProperties": False}


@tool("set_approval_policy", "Add approval rules (ask a human, or deny) for tool calls, as a new version. By "
      "default the rules are added to production's; replace=true replaces them.",
      {"slug": SLUG, "rules": {"type": "array", "items": RULE}, "replace": {"type": "boolean"},
       "timeout_seconds": {"type": "integer", "description": "How long a run waits for a decision (default 900)."}},
      ["slug", "rules"], "write", _d_policy)
async def set_approval_policy(ctx: ToolContext, a: dict) -> ToolResult:
    prod = db.get_version(agent_row(a["slug"])["prod_version_id"]) or {}
    v = await ctx.api.post(f"/agents/{a['slug']}/versions",
                           {"approval_policy": _merged_policy(prod, a),
                            "rationale": "Approval rules set by the Forge assistant"})
    return ToolResult(f"Created v{v['n']} with {len(v['approval_policy'].get('rules', []))} approval rules (not "
                      f"promoted; promote v{v['n']} to apply).", f"created v{v['n']}",
                      [{"label": f"v{v['n']}", "href": agent_path(a["slug"], "configure")}], [f"agent:{a['slug']}"])


@tool("set_budget", "Set or remove an agent's monthly budget and/or the per-run spending cap (null removes).",
      {"slug": SLUG, "monthly_budget_usd": {"type": ["number", "null"]}, "max_run_cost_usd": {"type": ["number", "null"]}},
      ["slug"], "write", _d_budget)
async def set_budget(ctx: ToolContext, a: dict) -> ToolResult:
    out, links = [], [{"label": "Configure", "href": agent_path(a["slug"], "configure")}]
    if "monthly_budget_usd" in a:
        ag = await ctx.api.call("PATCH", f"/agents/{a['slug']}", json={"monthly_budget_usd": a["monthly_budget_usd"]})
        out.append(f"monthly budget: {usd(ag['monthly_budget_usd']) if ag['monthly_budget_usd'] is not None else 'none'}")
    if "max_run_cost_usd" in a:
        v = await ctx.api.post(f"/agents/{a['slug']}/versions", {"max_run_cost_usd": a["max_run_cost_usd"],
                                                                 "rationale": "Spending cap set by the Forge assistant"})
        out.append(f"created v{v['n']} with per-run cap {usd(v['max_run_cost_usd']) if v['max_run_cost_usd'] is not None else 'none'} (not promoted)")
    return ToolResult("; ".join(out), "; ".join(out), links, [f"agent:{a['slug']}"])


@tool("create_trigger", "Add a schedule trigger (cron) that starts runs of an agent. Only schedules: GitHub and Slack "
      "triggers need secrets and are set up on the Deploy tab.",
      {"slug": SLUG, "cron": {"type": "string", "description": "5 cron fields or a macro such as @daily."},
       "input_template": {"type": "string", "description": "Run input; may use {date} {time} {datetime} {weekday}."},
       "timezone": {"type": "string", "description": "IANA time zone, default UTC."},
       "enabled": {"type": "boolean"}}, ["slug", "cron", "input_template"], "write", _d_trigger)
async def create_trigger(ctx: ToolContext, a: dict) -> ToolResult:
    t = await ctx.api.post(f"/agents/{a['slug']}/triggers", {"kind": "schedule", "config": _schedule_config(a),
                                                             "enabled": a.get("enabled", True)})
    nxt = t.get("next_fire_times") or []
    return ToolResult(f"Created schedule {t['id']}; next fire times (unix): {nxt[:3]}", f"created {t['id']}",
                      [{"label": "Deploy", "href": agent_path(a["slug"], "deploy")}], [f"triggers:{a['slug']}"])


@tool("save_run_as_eval", "Save a run's input as a new eval case (default: a judge rubric built from its feedback).",
      {"run_id": RUN_ID, "name": {"type": "string"}, "split": {"type": "string", "enum": ["train", "holdout"]},
       "checker_type": {"type": "string", "enum": ["shell", "judge"]}, "checker": {"type": "string"}},
      ["run_id"], "write", _d_save_as_eval)
async def save_run_as_eval(ctx: ToolContext, a: dict) -> ToolResult:
    body = {k: a[k] for k in ("name", "split", "checker_type", "checker") if a.get(k)}
    c = await ctx.api.post(f"/runs/{a['run_id']}/save-as-eval", body)
    slug = (db.get_agent(c["agent_id"]) or {}).get("slug", "")
    return ToolResult(f"Created {c['id']} {c['name']} [{c['split']}]", f"created {c['name']}",
                      [{"label": "Evaluations", "href": agent_path(slug, "evals")}], [f"evals:{slug}"])


@tool("add_feedback", "Rate a run thumbs up (1) or down (-1) with an optional comment.",
      {"run_id": RUN_ID, "rating": {"type": "integer", "enum": [1, -1]}, "comment": {"type": "string"}},
      ["run_id", "rating"], "write", _d_feedback)
async def add_feedback(ctx: ToolContext, a: dict) -> ToolResult:
    f = await ctx.api.post(f"/runs/{a['run_id']}/feedback", {"rating": a["rating"], "comment": a.get("comment") or ""})
    return ToolResult(f"Saved feedback {f['id']}.", "feedback saved", [{"label": a["run_id"], "href": f"/runs/{a['run_id']}"}],
                      [f"run:{a['run_id']}"])


def validate_args(t: CopilotTool, args: dict[str, Any]) -> str | None:
    """Required keys, no unknown keys, primitive types and enums. Returns a problem or None."""
    if "_raw" in args:
        return f"arguments for {t.name} were not a valid JSON object"
    props = t.parameters.get("properties", {})
    unknown = [k for k in args if k not in props]
    if unknown:
        return f"unknown argument(s) for {t.name}: {', '.join(unknown)}"
    missing = [k for k in t.parameters.get("required", []) if args.get(k) in (None, "")]
    if missing:
        return f"missing required argument(s) for {t.name}: {', '.join(missing)}"
    types = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict, "number": (int, float)}
    for k, v in args.items():
        want = props[k].get("type")
        allowed = want if isinstance(want, list) else [want]
        if v is None and "null" in allowed:
            continue
        ok = any(w is None or (isinstance(v, types[w]) and not (w in ("integer", "number") and isinstance(v, bool)))
                 for w in allowed if w != "null")
        if not ok:
            return f"argument {k!r} of {t.name} must be {' or '.join(str(w) for w in allowed)}"
        if "enum" in props[k] and v not in props[k]["enum"]:
            return f"argument {k!r} of {t.name} must be one of {props[k]['enum']}"
    return None

