"""Slack slash commands and Events API (`app_mention`): parsing and posting results back."""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from typing import Any

from forge.triggers import text
from forge.triggers.core import post_json, run_url
from forge.triggers.security import api_base_allowed, api_base_error

DEFAULT_RESPONSE_PREFIXES = ("https://hooks.slack.com/",)
_seen_events: "OrderedDict[str, None]" = OrderedDict()


def response_url_allowed(url: str) -> bool:
    """Only post to Slack's own response URLs unless the operator widens it (tests point it at a local receiver)."""
    raw = os.environ.get("FORGE_SLACK_RESPONSE_URL_PREFIXES")
    prefixes = tuple(p.strip() for p in raw.split(",") if p.strip()) if raw else DEFAULT_RESPONSE_PREFIXES
    return bool(url) and url.startswith(prefixes)


def seen_event(trigger_id: str, event_id: str) -> bool:
    """True if this Events API event was already handled (Slack retries deliveries it thinks timed out)."""
    key = f"{trigger_id}:{event_id}"
    if key in _seen_events:
        return True
    _seen_events[key] = None
    while len(_seen_events) > 2000:
        _seen_events.popitem(last=False)
    return False


def strip_mentions(s: str) -> str:
    return re.sub(r"<@[A-Z0-9]+(\|[^>]*)?>", "", s or "").strip()


def slash_fields(form: dict[str, str]) -> dict[str, Any]:
    return {"text": (form.get("text") or "").strip(), "user": form.get("user_name") or form.get("user_id") or "",
            "user_id": form.get("user_id") or "", "channel": form.get("channel_name") or form.get("channel_id") or "",
            "channel_id": form.get("channel_id") or "", "command": form.get("command") or "",
            "team": form.get("team_domain") or form.get("team_id") or ""}


def mention_fields(event: dict, team: str = "") -> dict[str, Any]:
    return {"text": strip_mentions(event.get("text") or ""), "user": event.get("user") or "",
            "user_id": event.get("user") or "", "channel": event.get("channel") or "",
            "channel_id": event.get("channel") or "", "command": "", "team": team}


SAMPLE = {"text": "This is a test message sent from Forge.", "user": "forge-test", "user_id": "U000",
          "channel": "test", "channel_id": "C000", "command": "/forge", "team": "forge"}


def build_input(cfg: dict, fields: dict[str, Any]) -> str:
    return text.render((cfg.get("input_template") or "").strip() or "{text}", fields)


async def deliver_response_url(t: dict, run: dict, ctx: dict) -> tuple[bool, str]:
    url = ctx.get("response_url") or ""
    if not response_url_allowed(url):
        return False, "response_url is not a Slack URL"
    msg = text.slack_message(run["output"] or run.get("error") or "", run["status"],
                             run_url(ctx.get("base_url"), run["id"]))
    body = {"response_type": t["config"].get("response_type") or "in_channel", "replace_original": False,
            "text": msg, "mrkdwn": True}
    return await post_json(url, body)


async def deliver_thread(t: dict, run: dict, ctx: dict) -> tuple[bool, str]:
    token = (t["tokens"] or {}).get("bot_token")
    if not token:
        return False, "skipped"
    base = (t["config"].get("api_base") or "https://slack.com/api").rstrip("/")
    if not api_base_allowed("slack", base):  # checked again at call time: the allowlist may have shrunk
        return False, api_base_error("slack", base)
    msg = text.slack_message(run["output"] or run.get("error") or "", run["status"],
                             run_url(ctx.get("base_url"), run["id"]))
    body = {"channel": ctx["channel"], "thread_ts": ctx["thread_ts"], "text": msg, "mrkdwn": True}
    return await post_json(f"{base}/chat.postMessage", body,
                           {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
                           check_ok_field=True)


async def post_thread_text(t: dict, ctx: dict, message: str) -> tuple[bool, str]:
    """A plain note in the thread (a busy or full conversation), best effort."""
    token = (t["tokens"] or {}).get("bot_token")
    base = (t["config"].get("api_base") or "https://slack.com/api").rstrip("/")
    if not token or not api_base_allowed("slack", base):
        return False, "skipped"
    body = {"channel": ctx["channel"], "thread_ts": ctx["thread_ts"], "text": message}
    return await post_json(f"{base}/chat.postMessage", body,
                           {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
                           check_ok_field=True)
