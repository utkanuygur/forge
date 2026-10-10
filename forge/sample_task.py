"""Sample tasks: one realistic task a real user of an agent would ask, written by the grader model.

Pure logic (context building, prompt, validation, duplicate detection, retry). The HTTP route lives in
forge.server.sample_task; it wraps the grader with `StructuredGrader` (structured output on Claude) and the
observability `MeteredProvider` so each call is recorded in `grader_calls` with kind "sample_task".
"""

from __future__ import annotations

import csv
import io
import json
import re
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from forge.types import Message, ModelResponse, Provider, ToolSpec

MAX_PROMPT_CHARS = 1200
MAX_TITLE_CHARS = 100
MAX_LISTED_FILES = 200
MAX_EXCERPT_TOTAL = 12_000
MAX_EXCERPT_CHARS = 1_800
MAX_SYSTEM_PROMPT_CHARS = 6_000
MAX_RECENT = 10
MAX_AVOID = 20
DUPLICATE_THRESHOLD = 0.85

# Cycled by the number of earlier generations for the agent so consecutive calls vary in type and difficulty.
VARIETY = (
    "a focused fix of one specific, concrete problem (medium difficulty)",
    "a quick question that needs reading a few files to answer precisely (easy)",
    "a small new capability or endpoint that follows the existing conventions (medium)",
    "an investigation or report built from the data, logs or code, with exact figures (medium)",
    "a multi-step change that touches several files and must be verified, e.g. with the tests (hard)",
    "a review or audit for one class of mistakes, with concrete findings and fixes (medium)",
)


class SampleTaskError(Exception):
    """The model did not produce a valid sample task after one retry. `errors` lists what was wrong."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or []


# ---------------------------------------------------------------- prompt

SAMPLE_TASK_SYSTEM = """You write realistic sample tasks for an AI agent so its builder and colleagues can try it \
out. The agent runs in an isolated Linux sandbox whose working directory holds the files listed in the request; \
it works with the tools listed there and ends with a written reply.

Write ONE task that a real user of this agent would plausibly send next. Requirements:
- Concrete and specific: name the real files, functions, endpoints, columns or log entries from the workspace \
listing and excerpts. Never invent files, endpoints or data that the context does not show (a task may ask the \
agent to create a new file or endpoint, as long as it builds on what exists).
- Self-contained and solvable inside the sandbox with the given files and tools. If network access is off, the \
task must not need the internet, package downloads or external APIs. Never require credentials other than the \
configured secret names listed in the request.
- Fits the agent's purpose as described by its name, description and system prompt.
- Phrased as a request from a teammate, in 1 to 4 sentences, in plain language, without preamble like "Task:".
- Different from every recent input and every text in the avoid list, in substance and not just wording.
- Match the requested variety (type and difficulty) when it fits this agent; otherwise pick the nearest fit.

Also give a short title (2 to 8 words, no trailing period) and a one sentence rationale that says what the \
task exercises. The rationale must not quote the system prompt or reveal configuration details.

Respond with the JSON object only: {"title": "...", "prompt": "...", "rationale": "..."}"""

SAMPLE_TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "prompt": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["title", "prompt", "rationale"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- context


def condense_system_prompt(text: str, limit: int = MAX_SYSTEM_PROMPT_CHARS) -> str:
    """Long system prompts are summarized by excerpt: the opening (role, method) and the end (output, avoid)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.75)
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head].rstrip()}\n[... {omitted} chars omitted ...]\n{text[-tail:].lstrip()}"


_CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java", ".kt", ".php", ".cs", ".sql", ".sh"}
_CONFIG_NAMES = {"makefile", "pyproject.toml", "package.json", "cargo.toml", "go.mod", "dockerfile",
                 "requirements.txt", "setup.cfg", "contributing.md", "conventions.md"}
_DATA_EXT = {".csv", ".tsv"}
_LOG_EXT = {".log"}
_DOC_EXT = {".md", ".rst", ".txt"}
_SKIP_PARTS = {"node_modules", "__pycache__", ".git", ".venv", "dist", "build", ".pytest_cache"}


def _kind(path: str) -> str:
    p = PurePosixPath(path)
    name, ext = p.name.lower(), p.suffix.lower()
    if name.startswith("readme"):
        return "readme"
    if name in _CONFIG_NAMES:
        return "config"
    if ext in _DATA_EXT:
        return "data"
    if ext in _LOG_EXT or ("/logs/" in f"/{path.lower()}" and ext in _DOC_EXT):
        return "log"
    if ext in _CODE_EXT:
        return "code"
    if ext in _DOC_EXT:
        return "doc"
    return "other"


def file_score(path: str, content: str) -> float:
    """Higher is more informative for writing a task: READMEs, then top-level modules, configs, data headers,
    log openings, then docs and deeper code. Tests, lock files and generated files rank last."""
    p = PurePosixPath(path)
    depth = len(p.parts) - 1
    if depth and p.parts[0] in ("src", "lib", "app", "pkg", "cmd"):
        depth -= 1  # src/<package>/module.py counts as a top-level module
    kind = _kind(path)
    lower = path.lower()
    base = {"readme": 100, "config": 55, "data": 60, "log": 58, "code": 50, "doc": 40, "other": 10}[kind]
    if kind == "readme":
        base -= 15 * depth
    elif kind == "code":
        if depth <= 1:
            base += 15 - 5 * depth
        else:
            base -= 4 * (depth - 1)
        if p.name in ("__init__.py", "conftest.py"):
            base -= 25
        if "test" in lower:
            base -= 20
    if lower.endswith((".lock", "-lock.json", ".min.js", ".map")) or any(s in p.parts for s in _SKIP_PARTS):
        base -= 60
    if not content.strip():
        base -= 80
    return base - len(path) / 1000  # stable tiebreak: shorter paths first


def excerpt(path: str, content: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    """A short, representative slice: CSV header and two rows with the row count, the first lines of a log,
    otherwise the opening lines of the file."""
    kind = _kind(path)
    if kind == "data":
        lines = content.splitlines()
        delim = "\t" if path.lower().endswith(".tsv") else ","
        try:
            header = next(csv.reader(io.StringIO(lines[0]), delimiter=delim)) if lines else []
        except (csv.Error, StopIteration):
            header = []
        out = f"columns: {', '.join(header)}\nrows: {max(0, len(lines) - 1)}\n" + "\n".join(lines[1:3])
        return out[:limit]
    max_lines = 12 if kind == "log" else 45
    lines = content.splitlines()[:max_lines]
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0] if "\n" in text[:limit] else text[:limit]
    return text


def select_excerpts(seed_files: dict[str, str], total: int = MAX_EXCERPT_TOTAL) -> list[tuple[str, str]]:
    """Excerpts of the most informative files, best first, while the total stays within `total` chars."""
    ranked = sorted(seed_files.items(), key=lambda kv: -file_score(kv[0], kv[1] or ""))
    # The best file of each kind first (README, module, data, log, config...), so one kind cannot crowd out the
    # others, then the rest by score.
    firsts: dict[str, tuple[str, str]] = {}
    for kv in ranked:
        firsts.setdefault(_kind(kv[0]), kv)
    lead = [kv for kv in ranked if firsts.get(_kind(kv[0])) is kv]
    ranked = lead + [kv for kv in ranked if kv not in lead]
    out: list[tuple[str, str]] = []
    used = 0
    for path, content in ranked:
        if file_score(path, content or "") < 20:
            continue
        ex = excerpt(path, content or "")
        if not ex.strip():
            continue
        cost = len(ex) + len(path) + 30
        if used + cost > total:
            room = total - used - len(path) - 30
            if room < 300:
                continue
            ex = ex[:room]
            cost = len(ex) + len(path) + 30
        out.append((path, ex))
        used += cost
        if used >= total - 300:
            break
    return out


def list_paths(seed_files: dict[str, str], limit: int = MAX_LISTED_FILES) -> tuple[list[str], int]:
    """Up to `limit` paths (most informative first, then the rest alphabetically) and the total count."""
    paths = sorted(seed_files)
    if len(paths) <= limit:
        return paths, len(paths)
    keep = sorted(paths, key=lambda p: -file_score(p, seed_files[p] or ""))[:limit]
    return sorted(keep), len(paths)


def _suggested(agent: dict) -> list[str]:
    raw = agent.get("suggested_prompts")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    out = []
    for s in raw or []:
        if isinstance(s, dict) and isinstance(s.get("prompt"), str):
            out.append(s["prompt"].strip())
        elif isinstance(s, str):
            out.append(s.strip())
    return [s for s in out if s][:10]


def _dedupe(items: list[str], limit: int) -> list[str]:
    seen, out = set(), []
    for s in items:
        s = (s or "").strip()
        key = normalize_text(s)
        if s and key not in seen:
            seen.add(key)
            out.append(s)
        if len(out) >= limit:
            break
    return out


def build_context(agent: dict, version: dict, *, recent_inputs: list[str], avoid: list[str], secret_names: list[str],
                  generation: int, hint: str | None = None) -> dict[str, Any]:
    """Everything the model sees, with caps applied. Pure, so it can be tested without a server."""
    seeds = version.get("seed_files") or {}
    paths, total_files = list_paths(seeds)
    return {
        "name": agent.get("name") or agent.get("slug") or "",
        "description": agent.get("description") or "",
        "system_prompt": condense_system_prompt(version.get("system_prompt") or ""),
        "system_prompt_chars": len(version.get("system_prompt") or ""),
        "tools": list(version.get("tools") or []),
        "http_tools": [t.get("name", "") for t in version.get("http_tools") or [] if isinstance(t, dict)],
        "network": bool(version.get("network")),
        "secret_names": sorted(secret_names),
        "files": paths,
        "total_files": total_files,
        "excerpts": select_excerpts(seeds),
        "suggested_prompts": _suggested(agent),
        "recent_inputs": _dedupe(recent_inputs, MAX_RECENT),
        "avoid": _dedupe(avoid, MAX_AVOID),
        "variety": VARIETY[generation % len(VARIETY)],
        "hint": (hint or "").strip()[:500],
    }


def _clip(s: str, n: int = 400) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."


def render_request(ctx: dict[str, Any]) -> str:
    parts = [f"<agent>\nname: {ctx['name']}\ndescription: {ctx['description'] or '(none)'}\n</agent>",
             f"<system_prompt>\n{ctx['system_prompt'] or '(empty)'}\n</system_prompt>"]
    net = "on (internet allowed)" if ctx["network"] else "off (no internet; the task must work offline)"
    env = [f"tools: {', '.join(ctx['tools']) or '(none)'}", f"network: {net}",
           f"configured secrets: {', '.join(ctx['secret_names']) or '(none)'}"]
    if ctx["http_tools"]:
        env.append(f"http tools: {', '.join(ctx['http_tools'])}")
    parts.append("<environment>\n" + "\n".join(env) + "\n</environment>")
    if ctx["files"]:
        more = f"\n({ctx['total_files'] - len(ctx['files'])} more files not listed)" \
            if ctx["total_files"] > len(ctx["files"]) else ""
        parts.append("<workspace_files>\n" + "\n".join(ctx["files"]) + more + "\n</workspace_files>")
    else:
        parts.append("<workspace_files>\n(the workspace starts empty; the task input must carry its own data)\n"
                     "</workspace_files>")
    for path, ex in ctx["excerpts"]:
        parts.append(f'<file_excerpt path="{path}">\n{ex}\n</file_excerpt>')
    if ctx["suggested_prompts"]:
        parts.append("<example_prompts>\n" + "\n".join(f"- {_clip(s)}" for s in ctx["suggested_prompts"])
                     + "\n</example_prompts>")
    if ctx["recent_inputs"]:
        parts.append("<recent_inputs>\n" + "\n".join(f"- {_clip(s)}" for s in ctx["recent_inputs"])
                     + "\n</recent_inputs>")
    if ctx["avoid"]:
        parts.append("<avoid>\n" + "\n".join(f"- {_clip(s)}" for s in ctx["avoid"]) + "\n</avoid>")
    parts.append(f"<variety>\n{ctx['variety']}\n</variety>")
    if ctx["hint"]:
        parts.append(f"<hint>\n{ctx['hint']}\n</hint>")
    parts.append("Write the sample task as the JSON object.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- validation


def normalize_text(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def similarity(a: str, b: str) -> float:
    """Similarity of normalized texts in [0, 1]: the larger of the character sequence ratio and the word set
    overlap (Jaccard), so both rewordings and reorderings are caught."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    seq = SequenceMatcher(None, na, nb).ratio()
    wa, wb = set(na.split()), set(nb.split())
    jac = len(wa & wb) / len(wa | wb) if wa | wb else 0.0
    return max(seq, jac)


def near_duplicate(prompt: str, others: list[str], threshold: float = DUPLICATE_THRESHOLD) -> str | None:
    """The first text in `others` that `prompt` nearly duplicates, else None."""
    for o in others:
        if similarity(prompt, o) >= threshold:
            return o
    return None


def parse_task(text: str) -> dict[str, str] | None:
    from forge.improve import _first_json_object

    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        obj = _first_json_object(text or "")
    if not isinstance(obj, dict):
        return None
    s = lambda v: v.strip() if isinstance(v, str) else ""  # noqa: E731
    return {"title": s(obj.get("title")), "prompt": s(obj.get("prompt")), "rationale": s(obj.get("rationale"))}


def validate_task(task: dict[str, str] | None, ctx: dict[str, Any]) -> list[str]:
    if task is None:
        return ["the reply was not a JSON object with title, prompt and rationale"]
    errs = []
    if not task["prompt"]:
        errs.append("prompt is empty")
    elif len(task["prompt"]) > MAX_PROMPT_CHARS:
        errs.append(f"prompt has {len(task['prompt'])} chars; keep it under {MAX_PROMPT_CHARS} (1 to 4 sentences)")
    if not task["title"]:
        errs.append("title is empty")
    elif len(task["title"]) > MAX_TITLE_CHARS:
        errs.append(f"title has {len(task['title'])} chars; use 2 to 8 words")
    if task["prompt"]:
        dup = near_duplicate(task["prompt"], ctx["recent_inputs"] + ctx["avoid"])
        if dup is not None:
            errs.append(f"prompt nearly duplicates an earlier input ({_clip(dup, 160)!r}); write a different task")
    return errs


# ---------------------------------------------------------------- model call


class StructuredGrader:
    """Provider adapter that asks Claude for JSON matching `schema` via structured outputs
    (`output_config.format`); other providers get the plain prompt and are parsed leniently. No temperature is
    sent (current Claude models reject sampling parameters). Wrap it in MeteredProvider to record usage."""

    def __init__(self, inner: Provider, schema: dict[str, Any]):
        self.inner, self.schema = inner, schema

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                       temperature: float | None = None) -> ModelResponse:
        from forge.providers import MAX_TOKENS, AnthropicProvider, with_retry

        if isinstance(self.inner, AnthropicProvider):
            kwargs: dict[str, Any] = {
                "model": self.inner.model, "max_tokens": MAX_TOKENS, "system": system,
                "messages": [{"role": "user", "content": m.text} for m in messages],
                "output_config": {"format": {"type": "json_schema", "schema": self.schema}},
            }
            resp = await with_retry(lambda: self.inner.client.messages.create(**kwargs))
            usage = getattr(resp, "usage", None)
            return ModelResponse(
                text="\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text"),
                tokens_in=getattr(usage, "input_tokens", 0) or 0, tokens_out=getattr(usage, "output_tokens", 0) or 0,
                stop_reason=getattr(resp, "stop_reason", None))
        return await self.inner.complete(system, messages, tools, temperature)


async def _ask(grader: Provider, user: str) -> str:
    resp = await grader.complete(SAMPLE_TASK_SYSTEM, [Message(role="user", text=user)], [], None)
    if getattr(resp, "stop_reason", None) == "refusal":
        raise SampleTaskError("The model declined to write a sample task for this agent.")
    return resp.text or ""


async def generate_sample_task(ctx: dict[str, Any], grader: Provider) -> dict[str, str]:
    """Returns {title, prompt, rationale}. One call; if the reply is invalid, one fresh retry listing the problems."""
    request = render_request(ctx)
    text = await _ask(grader, request)
    task = parse_task(text)
    errs = validate_task(task, ctx)
    if not errs:
        return task  # type: ignore[return-value]
    retry = (f"{request}\n\n<previous_attempt>\n{text[:4000]}\n</previous_attempt>\n\n<problems>\n"
             + "\n".join(f"- {e}" for e in errs)
             + "\n</problems>\n\nThe previous attempt was invalid. Return a corrected JSON object that fixes every "
               "problem.")
    text = await _ask(grader, retry)
    task = parse_task(text)
    errs = validate_task(task, ctx)
    if errs:
        raise SampleTaskError("The model could not write a valid sample task: " + "; ".join(errs[:4]), errs)
    return task  # type: ignore[return-value]
