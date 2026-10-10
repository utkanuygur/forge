"""Assist kind "seed_file": one realistic sample file for the agent's workspace (a CSV, a log, a small module).

Input (all optional): {"description": str (what the file should be), "path": str (the path to use),
"existing": [str] (paths already in the unsaved draft; added to the version's)}. Result:
{"path": str, "content": str, "description": str}. The web previews it; it is added only when the user saves.
"""

from __future__ import annotations

import ast
import csv
import io
import json
import re
from pathlib import PurePosixPath
from typing import Any

from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register

KIND = "seed_file"
MAX_CONTENT = 20_000
MAX_PATH = 200
PATH_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.\-/]*$")

SYSTEM = """You write sample files for the workspace of an AI agent, so the agent has realistic material to \
work on and its builder can try it out. The agent runs in an isolated Linux sandbox that starts with the files \
listed in the request.

Write ONE file that fits the agent's purpose and complements the existing files:
- Realistic and internally consistent: a CSV with a header row and 15 to 60 rows of plausible values (consistent \
column count, real looking but fictional names, dates and amounts, a few edge cases such as a missing value or an \
outlier); a log with 30 to 80 timestamped lines including a few warnings and errors with a pattern worth finding; \
code that is small, runnable and in the style of the existing modules, with one realistic flaw only when the \
description asks for it; docs or configs that match the existing project.
- Use a relative path that fits the existing layout (for example data/, logs/, src/<package>/) and does not \
replace an existing file unless that path was requested.
- Under 20000 characters. Never include real personal data, credentials or secret values (use obviously fake \
placeholders such as example.com addresses).
- Follow the description when one is given.

Put the path, the full content and one sentence describing the file in result. Respond with the JSON object \
only: {"result": {"path": "...", "content": "...", "description": "..."}, "rationale": "..."}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "description": {"type": "string"}},
    "required": ["path", "content", "description"],
    "additionalProperties": False,
}

SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[bap]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|"
                       r"-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def build_context(req: AssistRequest) -> Prepared:
    assert req.version is not None
    description = C.text_input(req.input, "description", 1000)
    path = C.text_input(req.input, "path", MAX_PATH)
    extra = [p for p in req.input.get("existing") or [] if isinstance(p, str)][:500]
    existing = sorted(set(req.version.get("seed_files") or {}) | set(extra))
    ctx, rendered = C.for_request(req)
    parts = [rendered]
    if extra:
        parts.append(C.bullets("unsaved_files", extra[:100]))
    req_lines = [f"description: {description}" if description else "description: (none; pick the most useful file)"]
    if path:
        req_lines.append(f"path: {path}")
    parts.append("<request>\n" + "\n".join(req_lines) + "\n</request>")
    parts.append("Write the file as the JSON object.")
    return Prepared(text="\n\n".join(parts), data={"existing": existing, "path": path},
                    diagnostics={"existing_files": len(existing), "description": bool(description),
                                 "path": path or None, "files_listed": len(ctx["files"])})


def check_content(path: str, content: str) -> list[str]:
    ext = PurePosixPath(path).suffix.lower()
    errs = []
    if ext in (".csv", ".tsv"):
        rows = list(csv.reader(io.StringIO(content), delimiter="\t" if ext == ".tsv" else ","))
        rows = [r for r in rows if r]
        if len(rows) < 3:
            errs.append("a CSV needs a header and at least two data rows")
        elif len({len(r) for r in rows}) != 1:
            bad = next(i for i, r in enumerate(rows) if len(r) != len(rows[0]))
            errs.append(f"CSV row {bad + 1} has {len(rows[bad])} columns, the header has {len(rows[0])}")
    elif ext == ".json":
        try:
            json.loads(content)
        except ValueError as exc:
            errs.append(f"the JSON does not parse: {exc}")
    elif ext == ".py":
        try:
            ast.parse(content)
        except SyntaxError as exc:
            errs.append(f"the Python does not parse: line {exc.lineno}: {exc.msg}")
    if SECRET_RE.search(content):
        errs.append("the content contains something that looks like a real credential; use a fake placeholder")
    return errs


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    path, content, desc = result.get("path"), result.get("content"), result.get("description")
    errs = []
    if not isinstance(path, str) or not path:
        errs.append("path is empty")
    elif len(path) > MAX_PATH or not PATH_RE.match(path) or ".." in PurePosixPath(path).parts or path.endswith("/"):
        errs.append(f"path {path!r} must be a relative file path without .. (letters, digits, . _ - /)")
    elif data.get("path") and path != data["path"]:
        errs.append(f"use the requested path {data['path']!r}")
    elif not data.get("path") and path in data.get("existing", []):
        errs.append(f"{path!r} already exists; pick a new path")
    if not isinstance(content, str) or not content.strip():
        errs.append("content is empty")
    elif len(content) > MAX_CONTENT:
        errs.append(f"content has {len(content)} chars; keep it under {MAX_CONTENT}")
    elif isinstance(path, str):
        errs += check_content(path, content)
    if not isinstance(desc, str) or not desc:
        errs.append("description is empty")
    return errs


FAKE_CSV = ("order_id,customer,region,total_cents,created_at\n" + "\n".join(
    f"{1000 + i},customer{i}@example.com,{('west', 'east', 'north')[i % 3]},{(i * 731) % 9000 + 500},"
    f"2026-09-{i % 28 + 1:02d}" for i in range(20)) + "\n")


def fake(user: str) -> dict[str, Any]:
    m = re.search(r"^path: (\S+)$", user, re.M)
    path = m.group(1) if m else "data/sample_orders.csv"
    n = 2
    while not m and (f"\n{path}\n" in user or f"- {path}\n" in user):
        path, n = f"data/sample_orders_{n}.csv", n + 1
    content = FAKE_CSV if path.endswith(".csv") else "# Sample file\n\nGenerated for trying out the agent.\n"
    return {"result": {"path": path, "content": content, "description": "Twenty sample orders across three regions."},
            "rationale": "Gives the agent realistic data to analyze."}


SPEC = register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=False, needs_agent=True, fake=fake,
                result_text=lambda r: str(r.get("path") or ""))
