"""Input templates and output formatting for triggers (Slack mrkdwn, GitHub comments)."""

from __future__ import annotations

import re

MAX_FIELD = 20_000
MAX_INPUT = 100_000
_PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([a-z_][a-z0-9_]*)\}")


def render(template: str, fields: dict[str, object]) -> str:
    """Replaces `{name}` with fields[name]. Unknown placeholders stay as written; `{{` and `}}` are literal braces.

    Deliberately not str.format: payload values never get attribute or index access.
    """

    def sub(m: re.Match) -> str:
        tok = m.group(0)
        if tok == "{{":
            return "{"
        if tok == "}}":
            return "}"
        name = m.group(1)
        if name not in fields:
            return tok
        value = fields[name]
        text = "" if value is None else str(value)
        return text[:MAX_FIELD]

    out = _PLACEHOLDER.sub(sub, template or "")
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out[:MAX_INPUT]


def truncate(text: str, limit: int, note: str = "") -> str:
    """Cuts `text` to at most `limit` characters at a line break when possible, closing an open code fence."""
    if len(text) <= limit:
        return text
    budget = max(0, limit - len(note) - 8)
    cut = text[:budget]
    nl = cut.rfind("\n")
    if nl > budget * 0.6:
        cut = cut[:nl]
    if cut.count("```") % 2 == 1:
        cut += "\n```"
    return cut.rstrip() + "\n..." + (f"\n{note}" if note else "")


# ---------------------------------------------------------------- Slack mrkdwn

def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(s: str) -> str:
    links: list[str] = []

    def keep_link(m: re.Match) -> str:
        links.append(f"<{m.group(2)}|{_escape(m.group(1))}>")
        return f"\x00{len(links) - 1}\x00"

    s = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", keep_link, s)
    s = _escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", "\x01\\1\x01", s)          # bold placeholder so italics do not eat it
    s = re.sub(r"__(.+?)__", "\x01\\1\x01", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"_\1_", s)
    s = re.sub(r"~~(.+?)~~", r"~\1~", s)
    s = s.replace("\x01", "*")
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], s)


def _line(line: str) -> str:
    if m := re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line):
        return f"*{_inline_code(m.group(1))}*"
    if m := re.match(r"^(\s*)[-*+]\s+(.*)$", line):
        return f"{m.group(1)}• {_inline_code(m.group(2))}"
    if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
        return "──────────"
    return _inline_code(line)


def _inline_code(line: str) -> str:
    parts = re.split(r"(`[^`]*`)", line)
    return "".join(p if p.startswith("`") and p.endswith("`") and len(p) > 1 else _inline(p) for p in parts)


def to_mrkdwn(md: str) -> str:
    """Converts common Markdown to Slack mrkdwn: bold, italics, strike, links, headings, bullets; code untouched."""
    out: list[str] = []
    in_code = False
    for line in (md or "").splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append("```")
            continue
        out.append(_escape(line) if in_code else _line(line))
    if in_code:
        out.append("```")
    return "\n".join(out)


SLACK_TEXT_LIMIT = 3500
GITHUB_COMMENT_LIMIT = 60_000


def slack_message(output: str, status: str, run_url: str) -> str:
    link = f"<{run_url}|View the run>"
    head = "" if status == "succeeded" else f"*Run {status}.*\n"
    body = to_mrkdwn(output.strip()) or "_The agent finished without output._"
    return head + truncate(body, SLACK_TEXT_LIMIT - len(head) - len(link) - 2, "Output truncated.") + "\n\n" + link


def github_comment(output: str, status: str, run_url: str, run_id: str) -> str:
    foot = f"\n\n---\n<sub>Forge run [{run_id}]({run_url})</sub>"
    head = "" if status == "succeeded" else f"**Run {status}.**\n\n"
    body = output.strip() or "_The agent finished without output._"
    return head + truncate(body, GITHUB_COMMENT_LIMIT - len(head) - len(foot), "Output truncated.") + foot
