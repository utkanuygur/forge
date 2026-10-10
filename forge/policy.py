"""Approval policy: which tool calls need a human decision (`ask`) or are refused outright (`deny`).

A policy is stored as JSON on each agent version (`agent_versions.approval_policy`):

    {"rules": [{"tool": "bash", "pattern": "\\bgit\\s+push\\b", "mode": "ask", "label": "git push"},
               {"tool": "write_file", "pattern": "*.env", "mode": "deny"},
               {"tool": "edit_file", "mode": "ask"}],
     "timeout_seconds": 900}

- `tool` is a tool name, or "*" for any tool (then `pattern` must be empty).
- `pattern` is optional. For `bash` it is a regular expression searched in the command. For the file
  tools it is a path glob: `*` stays inside one directory, `**` crosses directories, and a glob without
  a slash also matches the file name in any directory (`*.env` matches `config/prod.env`).
- No pattern means every call to that tool matches.
- A matching `deny` rule wins over any `ask` rule; otherwise the first matching `ask` rule is used.
- `timeout_seconds` (default 900, i.e. 15 minutes) auto-denies a pending approval.
An empty policy (`{}` or no rules) changes nothing.
"""

from __future__ import annotations

import posixpath
import re
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_TIMEOUT_SECONDS = 15 * 60
FILE_TOOLS = ("read_file", "write_file", "edit_file")
KNOWN_TOOLS = ("bash", *FILE_TOOLS)

# Presets offered in the UI (web/src/components/PolicyEditor.tsx mirrors these).
PRESETS: dict[str, list[dict[str, Any]]] = {
    "rm_rf": [{"tool": "bash", "pattern": r"\brm\s+(-\w+\s+)*-\w*([rR]\w*f|f\w*[rR])", "mode": "ask",
               "label": "rm -rf"}],
    "git_push": [{"tool": "bash", "pattern": r"\bgit\s+push\b", "mode": "ask", "label": "git push"}],
    "curl_wget": [{"tool": "bash", "pattern": r"\b(curl|wget)\b", "mode": "ask", "label": "curl or wget"}],
    "package_installs": [{"tool": "bash", "pattern": (
        r"\b(pip3?\s+install|uv\s+(pip\s+install|add)|npm\s+(install|i|add)|pnpm\s+(add|install)|yarn\s+add"
        r"|apt(-get)?\s+install|brew\s+install|cargo\s+(install|add)|gem\s+install)\b"),
        "mode": "ask", "label": "package installs"}],
    "env_files": [{"tool": t, "pattern": "*.env", "mode": "ask", "label": "env files"}
                  for t in ("write_file", "edit_file")],
    "migrations": [{"tool": t, "pattern": "migrations/**", "mode": "ask", "label": "migrations"}
                   for t in ("write_file", "edit_file")],
}


class Rule(BaseModel):
    tool: str
    pattern: str | None = None
    mode: Literal["ask", "deny"] = "ask"
    label: str | None = None

    @field_validator("pattern")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return v if v and v.strip() else None

    @model_validator(mode="after")
    def _check(self) -> "Rule":
        if self.tool != "*" and self.tool not in KNOWN_TOOLS:
            raise ValueError(f"unknown tool {self.tool!r}; use one of {', '.join(KNOWN_TOOLS)} or *")
        if self.tool == "*" and self.pattern:
            raise ValueError("a rule for every tool (*) cannot have a pattern")
        if self.tool == "bash" and self.pattern:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"invalid regular expression {self.pattern!r}: {exc}") from exc
        return self

    def describe(self) -> str:
        what = "any tool" if self.tool == "*" else self.tool
        if self.pattern:
            what += f" matching /{self.pattern}/" if self.tool == "bash" else f" on {self.pattern}"
        return f"{self.label}: {what}" if self.label else what


class Policy(BaseModel):
    rules: list[Rule] = Field(default_factory=list)
    timeout_seconds: int = Field(DEFAULT_TIMEOUT_SECONDS, ge=1, le=7 * 24 * 3600)


def parse_policy(raw: Any) -> Policy:
    """Validated policy from stored JSON (None or {} gives the empty policy). Raises ValueError when invalid."""
    if not raw:
        return Policy()
    if not isinstance(raw, dict):
        raise ValueError("approval_policy must be an object with a `rules` list")
    return Policy.model_validate(raw)


@lru_cache(maxsize=512)
def _glob_re(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        out.append({"*": "[^/]*", "?": "[^/]"}.get(c, re.escape(c)))
        i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(pattern: str, path: str) -> bool:
    pat = pattern.strip().removeprefix("./")
    norm = path.strip().replace("\\", "/")
    norm = posixpath.normpath(norm).lstrip("/").removeprefix("./") if norm else ""
    rx = _glob_re(pat)
    if rx.match(norm):
        return True
    # A bare glob (no slash) matches the file name in any directory, like .gitignore.
    return "/" not in pat and bool(rx.match(posixpath.basename(norm)))


def rule_matches(rule: Rule, name: str, args: dict[str, Any]) -> bool:
    if rule.tool != "*" and rule.tool != name:
        return False
    if not rule.pattern:
        return True
    if name == "bash":
        cmd = args.get("cmd", args.get("command"))
        return isinstance(cmd, str) and re.search(rule.pattern, cmd) is not None
    path = args.get("path")
    return isinstance(path, str) and glob_match(rule.pattern, path)


def match(policy: Policy, name: str, args: dict[str, Any]) -> tuple[int, Rule] | None:
    """(index, rule) of the rule that governs this call, or None when the call may run freely."""
    first_ask: tuple[int, Rule] | None = None
    for i, r in enumerate(policy.rules):
        if rule_matches(r, name, args):
            if r.mode == "deny":
                return i, r
            if first_ask is None:
                first_ask = (i, r)
    return first_ask


def rule_view(index: int, rule: Rule) -> dict[str, Any]:
    return {"index": index, **rule.model_dump(), "description": rule.describe()}
