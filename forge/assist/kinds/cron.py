"""Assist kind "cron": a 5 field cron expression and IANA time zone from a plain English schedule.

Input: {"text": str (required, e.g. "weekdays at 9am"), "timezone": str (optional default zone, e.g. the
browser's)}. Result: {"cron": str, "timezone": str, "explanation": str, "next_fire_times": [unix seconds] x3}
(`next_fire_times` is computed by the server with forge.triggers.cron). Works without an agent from localhost;
with `slug` the call is metered for the agent.
"""

from __future__ import annotations

import re
import time
from typing import Any

from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register
from forge.triggers.cron import Cron, CronError, get_zone

KIND = "cron"

SYSTEM = """You convert a schedule written in plain English into a standard 5 field cron expression (minute, \
hour, day of month, month, day of week; 0 or 7 is Sunday) and an IANA time zone, for a scheduled trigger that \
starts an AI agent run.

- Use the time zone named in the text (city, region or abbreviation mapped to its IANA name, e.g. "Pacific" \
to America/Los_Angeles); otherwise the default time zone given in the request.
- When the text gives no time of day, pick a sensible working hour (09:00); when it gives no minute, use 0.
- Prefer simple expressions (ranges like 1-5 and steps like */15) over long lists.
- explanation: one plain sentence restating when it fires, including the time zone.

Respond with the JSON object only: {"result": {"cron": "0 9 * * 1-5", "timezone": "America/Los_Angeles", \
"explanation": "..."}, "rationale": "..."}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"cron": {"type": "string"}, "timezone": {"type": "string"}, "explanation": {"type": "string"}},
    "required": ["cron", "timezone", "explanation"],
    "additionalProperties": False,
}


def build_context(req: AssistRequest) -> Prepared:
    text = C.text_input(req.input, "text", 500, required=True)
    tz = C.text_input(req.input, "timezone", 64) or "UTC"
    try:
        get_zone(tz)
    except CronError:
        tz = "UTC"
    parts = [f"<schedule>\n{text}\n</schedule>", f"<default_timezone>\n{tz}\n</default_timezone>",
             "Write the cron expression as the JSON object."]
    return Prepared(text="\n\n".join(parts), data={}, diagnostics={"default_timezone": tz})


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    errs = []
    try:
        Cron.parse(str(result.get("cron") or ""))
    except CronError as exc:
        errs.append(f"cron {result.get('cron')!r} is invalid: {exc}")
    try:
        get_zone(str(result.get("timezone") or ""))
    except CronError as exc:
        errs.append(str(exc))
    if not str(result.get("explanation") or "").strip():
        errs.append("explanation is empty")
    return errs


def finalize(result: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    expr = " ".join(str(result["cron"]).split())
    return {**result, "cron": expr,
            "next_fire_times": Cron.parse(expr).next_n(time.time(), 3, result["timezone"])}


DAYS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}


def fake(user: str) -> dict[str, Any]:
    """A tiny deterministic parser for tests: every N minutes, hourly, weekdays, a weekday name, at H(:MM)am/pm."""
    m = re.search(r"<schedule>\n(.*?)\n</schedule>", user, re.S)
    text = (m.group(1) if m else "").lower()
    tzm = re.search(r"<default_timezone>\n(.*?)\n</default_timezone>", user, re.S)
    tz = tzm.group(1) if tzm else "UTC"
    hour, minute = 9, 0
    if t := re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text):
        hour = int(t.group(1)) % 12 + (12 if t.group(3) == "pm" else 0)
        minute = int(t.group(2) or 0)
    elif t := re.search(r"\b(\d{1,2}):(\d{2})\b", text):
        hour, minute = int(t.group(1)), int(t.group(2))
    if n := re.search(r"every (\d+) minutes?", text):
        expr = f"*/{n.group(1)} * * * *"
    elif "every hour" in text or "hourly" in text:
        expr = "0 * * * *"
    elif "weekday" in text:
        expr = f"{minute} {hour} * * 1-5"
    elif day := next((d for d in DAYS if d in text), None):
        expr = f"{minute} {hour} * * {DAYS[day]}"
    else:
        expr = f"{minute} {hour} * * *"
    return {"result": {"cron": expr, "timezone": tz, "explanation": f"Fires on the schedule {expr} in {tz}."},
            "rationale": "Parsed from the schedule text."}


SPEC = register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=False, needs_agent=False, fake=fake,
                finalize=finalize)
