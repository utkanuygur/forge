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

from forge import db
from forge.server import runs
from forge.triggers import store
from forge.triggers.cron import Cron, CronError, get_zone
from forge.triggers.security import RateLimiter

log = logging.getLogger("forge.triggers")

KINDS = ("schedule", "github", "slack")
ACTIVE = {"queued", "running"}
limiter = RateLimiter()
_background: set[asyncio.Task] = set()


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
    try:
        return int(os.environ.get("FORGE_TRIGGER_RATE_LIMIT") or 30)
    except ValueError:
        return 30


def public_url() -> str | None:
    return (os.environ.get("FORGE_PUBLIC_URL") or "").rstrip("/") or None


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

    @model_validator(mode="after")
    def _command(self) -> "GithubConfig":
        if "issue_comment" in self.events and not self.command.strip():
            raise ValueError("a command such as /forge is required for issue_comment events")
        return self


class SlackConfig(Common):
    input_template: str = "{text}"
    response_type: Literal["in_channel", "ephemeral"] = "in_channel"
    api_base: str = "https://slack.com/api"


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
    status: str  # started | skipped | rate_limited | error
    detail: str = ""
    run_id: str | None = None
    fire: dict | None = None


def _record_error(t: dict, message: str) -> None:
    store.update(t["id"], last_error=message[:2000])


async def fire(t: dict, input_text: str, *, context: dict | None = None, test: bool = False,
               ignore_rate_limit: bool = False) -> Fired:
    """Starts one run for trigger `t`; records the outcome in trigger_fires and on the trigger row."""
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
    run_id = await runs.get_manager().start_run(agent, version, input_text, t["kind"])
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
