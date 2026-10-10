"""Shared agent context for assist kinds: what the grader model knows about an agent.

`agent_context` gathers the version's system prompt (condensed when very long), the agent's name and description,
tools, network setting, configured secret NAMES (never values), up to 200 seed file paths plus short excerpts of
the most informative files (at most 12k chars), the agent's suggested prompts and its recent run inputs.
`render_agent_context` turns it into the opening of an assist request. Text helpers for duplicate detection live
here too, so every kind can use them.
"""

from __future__ import annotations

import csv
import io
import json
import re
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

MAX_LISTED_FILES = 200
MAX_EXCERPT_TOTAL = 12_000
MAX_EXCERPT_CHARS = 1_800
MAX_SYSTEM_PROMPT_CHARS = 6_000
MAX_RECENT = 10
MAX_AVOID = 20
DUPLICATE_THRESHOLD = 0.85


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


def dedupe(items: list[str], limit: int) -> list[str]:
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


def agent_context(agent: dict, version: dict, *, recent_inputs: list[str], secret_names: list[str]) -> dict[str, Any]:
    """Everything a kind may show the model about the agent, with caps applied. Secret values never enter."""
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
        "recent_inputs": dedupe(recent_inputs, MAX_RECENT),
    }


def clip(s: str, n: int = 400) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."


def bullets(tag: str, items: list[str]) -> str:
    return f"<{tag}>\n" + "\n".join(f"- {clip(s)}" for s in items) + f"\n</{tag}>"


def render_agent_context(ctx: dict[str, Any]) -> str:
    """The agent, its system prompt, environment, workspace listing and excerpts, example and recent inputs."""
    parts = [f"<agent>\nname: {ctx['name']}\ndescription: {ctx['description'] or '(none)'}\n</agent>",
             f"<system_prompt>\n{ctx['system_prompt'] or '(empty)'}\n</system_prompt>"]
    net = "on (internet allowed)" if ctx["network"] else "off (no internet; work must happen offline)"
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
        parts.append("<workspace_files>\n(the workspace starts empty; a task input must carry its own data)\n"
                     "</workspace_files>")
    for path, ex in ctx["excerpts"]:
        parts.append(f'<file_excerpt path="{path}">\n{ex}\n</file_excerpt>')
    if ctx["suggested_prompts"]:
        parts.append(bullets("example_prompts", ctx["suggested_prompts"]))
    if ctx["recent_inputs"]:
        parts.append(bullets("recent_inputs", ctx["recent_inputs"]))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- text similarity


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
