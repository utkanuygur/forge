"""Trigger configuration, views, firing (rate limit, version pin, run start) and output delivery."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forge import db, settings
from forge.types import ACTIVE_STATUSES
from forge.server import runs
from forge.triggers import store
from forge.triggers.cron import Cron, CronError, get_zone
from forge.triggers.security import RateLimiter, api_base_allowed, api_base_error

log = logging.getLogger("forge.triggers")

KINDS = ("schedule", "github", "slack")
ACTIVE = ACTIVE_STATUSES  # a run paused on an approval is still running
limiter = RateLimiter()
_background: set[asyncio.Task] = set()

settings.register_setting(
    key="trigger_rate_limit", env="FORGE_TRIGGER_RATE_LIMIT", type="int", default=30, min=1, max=10000,
    group="Triggers", label="Trigger rate limit", description="Runs per hour each trigger may start.")


def now() -> float:
    """Wall clock for the scheduler. Tests may point FORGE_TEST_CLOCK_FILE at a file holding unix seconds."""
    path = os.environ.get("FORGE_TEST_CLOCK_FILE")
    if path:
        try:
            return float(open(path).read().strip())
        except (OSError, ValueError):
            pass
    return time.time()


def default_rate() -> int:
    return int(settings.get("trigger_rate_limit"))


def public_url() -> str | None:
    return (settings.get("public_url") or "").rstrip("/") or None


def run_url(base: str | None, run_id: str) -> str:
    return f"{(public_url() or base or 'http://localhost:8000').rstrip('/')}/runs/{run_id}"


def spawn(coro: Any) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


# ---------------------------------------------------------------- configuration


class Common(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_template: str = ""
    version: int | None = Field(default=None, ge=1)
    max_runs_per_hour: int = Field(default_factory=default_rate, ge=1, le=100_000)


class ScheduleConfig(Common):
    cron: str
    timezone: str = "UTC"
    skip_if_running: bool = True

    @field_validator("cron")
    @classmethod
    def _cron(cls, v: str) -> str:
        Cron.parse(v)
        return " ".join(v.split())

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        get_zone(v)
        return v or "UTC"

    @model_validator(mode="after")
    def _template(self) -> "ScheduleConfig":
        if not self.input_template.strip():
            raise ValueError("input_template is required for schedules")
        return self


GithubEvent = Literal["issues", "issue_comment", "pull_request", "push"]


class GithubConfig(Common):
    events: list[GithubEvent] = Field(default_factory=lambda: ["issues", "issue_comment", "pull_request"])
    label: str = ""
    command: str = "/forge"
    branches: list[str] = Field(default_factory=list)
    pr_actions: list[Literal["opened", "synchronize", "reopened"]] = Field(
        default_factory=lambda: ["opened", "synchronize"])
    comment_back: bool = True
    api_base: str = "https://api.github.com"

    @field_validator("api_base")
    @classmethod
    def _api_base(cls, v: str) -> str:
        v = (v or "https://api.github.com").strip()
        if not api_base_allowed("github", v):
            raise ValueError(api_base_error("github", v))
        return v

    @model_validator(mode="after")
    def _command(self) -> "GithubConfig":
        if "issue_comment" in self.events and not self.command.strip():
            raise ValueError("a command such as /forge is required for issue_comment events")
        return self


class SlackConfig(Common):
    input_template: str = "{text}"
    response_type: Literal["in_channel", "ephemeral"] = "in_channel"
    api_base: str = "https://slack.com/api"

    @field_validator("api_base")
    @classmethod
    def _api_base(cls, v: str) -> str:
        v = (v or "https://slack.com/api").strip()
        if not api_base_allowed("slack", v):
            raise ValueError(api_base_error("slack", v))
        return v


CONFIGS: dict[str, type[Common]] = {"schedule": ScheduleConfig, "github": GithubConfig, "slack": SlackConfig}


def validate_config(kind: str, config: dict) -> dict:
    """Returns the normalized config or raises ValueError with a readable message."""
    from pydantic import ValidationError

    try:
        return CONFIGS[kind](**config).model_dump()
    except CronError as exc:
        raise ValueError(str(exc)) from exc
    except ValidationError as exc:
        msgs = []
        for e in exc.errors():
            where = ".".join(str(x) for x in e["loc"])
            msgs.append(f"{where}: {e['msg']}" if where else e["msg"])
        raise ValueError("; ".join(m.replace("Value error, ", "") for m in msgs)) from exc


def hook_path(t: dict) -> str | None:
    return f"/v1/hooks/{t['kind']}/{t['id']}" if t["kind"] in ("github", "slack") else None


def trigger_view(t: dict) -> dict:
    out = {
        "id": t["id"], "agent_id": t["agent_id"], "kind": t["kind"], "config": t["config"], "enabled": t["enabled"],
        "created_at": t["created_at"], "last_fired_at": t["last_fired_at"], "last_run_id": t["last_run_id"],
        "last_error": t["last_error"], "has_secret": bool(t["secret"]),
        "has_token": bool((t["tokens"] or {}).get("github_token") or (t["tokens"] or {}).get("bot_token")),
        "hook_path": hook_path(t), "public_url": public_url(),
    }
    if t["kind"] == "schedule":
        try:
            cfg = t["config"]
            out["next_fire_times"] = Cron.parse(cfg["cron"]).next_n(now(), 3, cfg.get("timezone") or "UTC") \
                if t["enabled"] else []
        except (CronError, KeyError):
            out["next_fire_times"] = []
    return out


def fire_view(f: dict) -> dict:
    from forge.server.views import run_view

    r = db.get_run(f["run_id"]) if f["run_id"] else None
    return {"id": f["id"], "trigger_id": f["trigger_id"], "status": f["status"], "detail": f["detail"],
            "test": f["test"], "run_id": f["run_id"], "delivery": f["delivery"], "delivery_error": f["delivery_error"],
            "created_at": f["created_at"], "run": run_view(r) if r else None}


# ---------------------------------------------------------------- firing


@dataclass
class Fired:
    status: str  # started | skipped | rate_limited | error (skipped: a Slack thread is busy or full)
    detail: str = ""
    run_id: str | None = None
    fire: dict | None = None


def _record_error(t: dict, message: str) -> None:
    store.update(t["id"], last_error=message[:2000])


def thread_key(trigger_id: str, channel: str, thread_ts: str) -> str:
    return f"slack:{trigger_id}:{channel}:{thread_ts}"


def thread_session(t: dict, agent: dict, version: dict, key: str) -> tuple[dict | None, str]:
    """The conversation for a Slack thread: the thread's session unless it expired (then a fresh one). Returns
    (None, reason) when the thread already used the agent's per conversation turn limit or a turn still runs."""
    from forge import share_limits

    s = db.session_by_key(key)
    if s is not None and share_limits.is_expired(s) and not any(
            r["status"] in ACTIVE for r in db.session_runs(s["id"])):
        s = None
    if s is None:
        return db.create_session(agent["id"], version["id"], version_id=version["id"], channel="slack",
                                 external_key=key), ""
    if any(r["status"] in ACTIVE for r in db.session_runs(s["id"])):
        return None, "Still working on the previous message in this thread. Mention me again once it is done."
    limit = share_limits.effective(agent)["session_turns"]
    if db.session_turn_count(s["id"]) >= limit:
        return None, (f"This thread reached its limit of {limit} messages. Mention me in a new thread to start a "
                      "new conversation.")
    return s, ""


async def fire(t: dict, input_text: str, *, context: dict | None = None, test: bool = False,
               ignore_rate_limit: bool = False, session_key: str | None = None) -> Fired:
    """Starts one run for trigger `t`; records the outcome in trigger_fires and on the trigger row.
    With `session_key` (a Slack thread) the run is the next turn of that thread's conversation."""
    cfg = t["config"]
    agent = db.get_agent(t["agent_id"])
    if agent is None:
        return Fired("error", "agent not found")
    if not ignore_rate_limit and not limiter.allow(t["id"], int(cfg.get("max_runs_per_hour") or default_rate())):
        detail = f"rate limit of {cfg.get('max_runs_per_hour')} runs per hour reached"
        f = store.add_fire(t["id"], "rate_limited", detail=detail, test=test)
        _record_error(t, detail)
        return Fired("rate_limited", detail, fire=f)
    pin = cfg.get("version")
    version = db.get_version_n(agent["id"], pin if pin else "prod")
    if version is None:
        detail = f"version {pin} not found" if pin else "agent has no production version"
        f = store.add_fire(t["id"], "error", detail=detail, test=test)
        _record_error(t, detail)
        return Fired("error", detail, fire=f)
    if not input_text.strip():
        detail = "the input template rendered to an empty input"
        f = store.add_fire(t["id"], "error", detail=detail, test=test)
        _record_error(t, detail)
        return Fired("error", detail, fire=f)
    from forge.budget import BudgetExceeded

    session = None
    if session_key:
        session, why = thread_session(t, agent, version, session_key)
        if session is None:
            f = store.add_fire(t["id"], "skipped", detail=why, test=test)
            return Fired("skipped", why, fire=f)
    try:  # trigger runs go through budgets like any other run, and through approval policies once started
        if session is not None:
            from forge.server.sessions_api import start_turn

            run_id = await start_turn(session, agent, input_text, t["kind"], version=version)
        else:
            run_id = await runs.get_manager().start_run(agent, version, input_text, t["kind"])
    except BudgetExceeded as exc:
        detail = f"402: {exc}"
        f = store.add_fire(t["id"], "error", detail=detail, test=test)
        _record_error(t, detail)
        return Fired("error", detail, fire=f)
    f = store.add_fire(t["id"], "started", run_id=run_id, test=test, context=context or {})
    store.update(t["id"], last_fired_at=time.time(), last_run_id=run_id, last_error=None)
    if context and context.get("deliver") and not test:
        spawn(_deliver_when_done(t["id"], f["id"], run_id, context))
    return Fired("started", run_id=run_id, fire=f)


async def _deliver_when_done(trigger_id: str, fire_id: str, run_id: str, context: dict) -> None:
    from forge.triggers import github, slack

    run = await runs.get_manager().wait(run_id)
    t = store.get(trigger_id)
    if run is None or t is None:
        return
    handlers = {"github_comment": github.deliver, "slack_response_url": slack.deliver_response_url,
                "slack_thread": slack.deliver_thread}
    handler = handlers.get(context["deliver"])
    if handler is None:
        return
    try:
        ok, error = await handler(t, run, context)
    except Exception as exc:  # noqa: BLE001
        log.exception("delivery failed for %s", run_id)
        ok, error = False, f"{type(exc).__name__}: {exc}"
    if ok:
        store.update_fire(fire_id, delivery="delivered", delivery_error=None)
    elif error == "skipped":
        store.update_fire(fire_id, delivery="skipped", delivery_error=None)
    else:
        store.update_fire(fire_id, delivery="failed", delivery_error=error)
        _record_error(t, f"Posting the result failed: {error}")


def retry_backoff() -> float:
    try:
        return float(os.environ.get("FORGE_TRIGGER_RETRY_BACKOFF") or 1.0)
    except ValueError:
        return 1.0


async def post_json(url: str, body: dict, headers: dict[str, str] | None = None, *, retries: int = 3,
                    check_ok_field: bool = False) -> tuple[bool, str]:
    """POST with retries on network errors, 429 and 5xx. Returns (ok, error message)."""
    error = ""
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(retries + 1):
            try:
                r = await client.post(url, json=body, headers=headers or {})
                if r.status_code < 300:
                    if check_ok_field:
                        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                        if not data.get("ok", False):
                            error = f"API error: {data.get('error') or 'unknown'}"
                            if data.get("error") != "ratelimited":
                                return False, error
                        else:
                            return True, ""
                    else:
                        return True, ""
                else:
                    error = f"HTTP {r.status_code}: {r.text[:200]}"
                    if r.status_code < 500 and r.status_code != 429:
                        return False, error
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                await asyncio.sleep(retry_backoff() * 2**attempt)
    return False, error
