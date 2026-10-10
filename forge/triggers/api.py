"""HTTP routes for triggers: builder CRUD (localhost or API key) and signed public hooks (GitHub, Slack)."""

from __future__ import annotations

import json
import time
from typing import Any, Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from forge import db
from forge.server.auth import authorize
from forge.triggers import core, github, scheduler, slack, store, text
from forge.triggers.security import new_secret, verify_github, verify_slack

router = APIRouter(prefix="/v1")
MAX_BODY = 5_000_000
TOKEN_KEY = {"github": "github_token", "slack": "bot_token"}

# ---------------------------------------------------------------- builder


class TriggerIn(BaseModel):
    kind: Literal["schedule", "github", "slack"]
    config: dict[str, Any] = {}
    enabled: bool = True
    signing_secret: str | None = None  # Slack: the app's signing secret
    token: str | None = None           # GitHub token for comment-back, or Slack bot token for app mentions


class TriggerPatch(BaseModel):
    config: dict[str, Any] | None = None  # merged over the current config
    enabled: bool | None = None
    signing_secret: str | None = None
    token: str | None = None              # "" removes it


class TestIn(BaseModel):
    input: str | None = None


def _agent(slug: str, request: Request) -> dict:
    agent = db.get_agent_by_slug(slug)
    if agent is None:
        raise HTTPException(404, f"agent {slug!r} not found")
    authorize(request, agent, allow_share=False)
    return agent


def _trigger(trigger_id: str, request: Request) -> dict:
    t = store.get(trigger_id)
    if t is None:
        raise HTTPException(404, "trigger not found")
    authorize(request, db.get_agent(t["agent_id"]), allow_share=False)  # type: ignore[arg-type]
    return t


def _config(kind: str, config: dict, agent: dict) -> dict:
    try:
        cfg = core.validate_config(kind, config)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if cfg.get("version") and db.get_version_n(agent["id"], cfg["version"]) is None:
        raise HTTPException(400, f"version {cfg['version']} not found")
    return cfg


@router.get("/agents/{slug}/triggers")
async def list_triggers(slug: str, request: Request) -> list[dict]:
    agent = _agent(slug, request)
    return [core.trigger_view(t) for t in store.list_for_agent(agent["id"])]


@router.post("/agents/{slug}/triggers")
async def create_trigger(slug: str, body: TriggerIn, request: Request) -> dict:
    agent = _agent(slug, request)
    cfg = _config(body.kind, body.config, agent)
    secret = None
    if body.kind == "github":
        secret = new_secret()
    elif body.kind == "slack":
        secret = (body.signing_secret or "").strip() or None
    tokens = {TOKEN_KEY[body.kind]: body.token.strip()} if body.token and body.kind in TOKEN_KEY else {}
    t = store.create(agent["id"], body.kind, cfg, secret=secret, tokens=tokens, enabled=body.enabled,
                     last_slot_at=core.now())
    out = core.trigger_view(t)
    if body.kind == "github":
        out["secret"] = secret  # shown once
    return out


@router.get("/triggers/{trigger_id}")
async def get_trigger(trigger_id: str, request: Request) -> dict:
    return core.trigger_view(_trigger(trigger_id, request))


@router.patch("/triggers/{trigger_id}")
async def patch_trigger(trigger_id: str, body: TriggerPatch, request: Request) -> dict:
    t = _trigger(trigger_id, request)
    agent = db.get_agent(t["agent_id"])
    fields: dict[str, Any] = {}
    if body.config is not None:
        merged = {**t["config"], **body.config}
        fields["config"] = _config(t["kind"], merged, agent)  # type: ignore[arg-type]
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    if t["kind"] == "schedule":
        old, new = t["config"], fields.get("config", t["config"])
        if (body.enabled and not t["enabled"]) or (old.get("cron"), old.get("timezone")) != \
                (new.get("cron"), new.get("timezone")):
            fields["last_slot_at"] = core.now()  # start counting slots from now, never replay old ones
    if body.signing_secret is not None and t["kind"] == "slack":
        fields["secret"] = body.signing_secret.strip() or None
    if body.token is not None and t["kind"] in TOKEN_KEY:
        tokens = dict(t["tokens"] or {})
        if body.token.strip():
            tokens[TOKEN_KEY[t["kind"]]] = body.token.strip()
        else:
            tokens.pop(TOKEN_KEY[t["kind"]], None)
        fields["tokens"] = tokens
    if body.enabled:
        fields["last_error"] = None
    return core.trigger_view(store.update(t["id"], **fields))  # type: ignore[arg-type]


@router.delete("/triggers/{trigger_id}")
async def delete_trigger(trigger_id: str, request: Request) -> dict:
    t = _trigger(trigger_id, request)
    store.delete(t["id"])
    core.limiter.reset(t["id"])
    return {"ok": True}


@router.post("/triggers/{trigger_id}/rotate-secret")
async def rotate_secret(trigger_id: str, request: Request) -> dict:
    t = _trigger(trigger_id, request)
    if t["kind"] != "github":
        raise HTTPException(400, "only GitHub triggers have a generated secret")
    secret = new_secret()
    store.update(t["id"], secret=secret)
    return {"secret": secret}


@router.post("/triggers/{trigger_id}/test")
async def test_trigger(trigger_id: str, request: Request, body: TestIn = Body(default_factory=TestIn)) -> JSONResponse:
    """Fires once with a sample payload. Results are not posted to GitHub or Slack."""
    t = _trigger(trigger_id, request)
    cfg = t["config"]
    if body.input and body.input.strip():
        input_text = body.input.strip()
    elif t["kind"] == "schedule":
        input_text = text.render(cfg.get("input_template") or "",
                                 scheduler.template_fields(core.now(), cfg.get("timezone") or "UTC"))
    elif t["kind"] == "github":
        sample = dict(github.SAMPLE)
        sample["event"] = (cfg.get("events") or ["issues"])[0]
        sample["comment"] = "Please take a look at this." if sample["event"] == "issue_comment" else ""
        input_text = github.build_input(cfg, sample)
    else:
        input_text = slack.build_input(cfg, slack.SAMPLE)
    r = await core.fire(t, input_text, test=True)
    return _fired_response(r, request, extra={"input": input_text})


@router.get("/triggers/{trigger_id}/history")
async def history(trigger_id: str, request: Request, limit: int = Query(20, ge=1, le=200)) -> list[dict]:
    t = _trigger(trigger_id, request)
    return [core.fire_view(f) for f in store.list_fires(t["id"], limit)]


def _fired_response(r: core.Fired, request: Request | None = None, extra: dict | None = None) -> JSONResponse:
    if r.status == "rate_limited":
        key = r.fire["trigger_id"] if r.fire else ""
        return JSONResponse({"detail": r.detail}, status_code=429,
                            headers={"Retry-After": str(core.limiter.retry_after(key) or 60)})
    if r.status != "started":
        return JSONResponse({"detail": r.detail}, status_code=402 if r.detail.startswith("402:") else 409)
    base = str(request.base_url) if request else None
    return JSONResponse({"run_id": r.run_id, "run_url": core.run_url(base, r.run_id), **(extra or {})},  # type: ignore[arg-type]
                        status_code=202)


# ---------------------------------------------------------------- public hooks (signature checked, no other auth)


def _hook_trigger(kind: str, trigger_id: str) -> dict:
    t = store.get(trigger_id)
    if t is None or t["kind"] != kind:
        raise HTTPException(404, "trigger not found")
    return t


async def _body(request: Request) -> bytes:
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        raise HTTPException(413, "payload too large")
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "payload too large")
    return body


def _ignored(reason: str) -> Response:
    return Response(status_code=204, headers={"X-Forge-Ignored": reason.encode("ascii", "replace").decode()[:200]})


@router.post("/hooks/github/{trigger_id}", include_in_schema=False)
async def github_hook(trigger_id: str, request: Request) -> Response:
    t = _hook_trigger("github", trigger_id)
    body = await _body(request)
    if not verify_github(t["secret"], body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "invalid or missing X-Hub-Signature-256")
    event = request.headers.get("x-github-event", "")
    if event == "ping":
        return JSONResponse({"ok": True, "message": "Forge received the ping."})
    if not t["enabled"]:
        return _ignored("trigger is disabled")
    try:
        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            payload = json.loads(parse_qs(body.decode()).get("payload", ["{}"])[0])
        else:
            payload = json.loads(body or b"{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "payload must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload must be a JSON object")
    fields, why = github.parse(event, payload, t["config"])
    if fields is None:
        return _ignored(why)
    has_token = bool((t["tokens"] or {}).get("github_token"))
    ctx = github.delivery_context(fields, t["config"], has_token, str(request.base_url))
    r = await core.fire(t, github.build_input(t["config"], fields), context=ctx)
    return _fired_response(r, request)


def _ephemeral(msg: str) -> JSONResponse:
    return JSONResponse({"response_type": "ephemeral", "text": msg})


@router.post("/hooks/slack/{trigger_id}", include_in_schema=False)
async def slack_hook(trigger_id: str, request: Request) -> Response:
    t = _hook_trigger("slack", trigger_id)
    body = await _body(request)
    ok, why = verify_slack(t["secret"], body, request.headers.get("x-slack-request-timestamp"),
                           request.headers.get("x-slack-signature"), now=time.time())
    if not ok:
        raise HTTPException(401, why)
    base = str(request.base_url)
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise HTTPException(400, "payload must be JSON") from exc
        if data.get("type") == "url_verification":
            return JSONResponse({"challenge": data.get("challenge", "")})
        ev = data.get("event") or {}
        if data.get("type") != "event_callback" or ev.get("type") != "app_mention" or ev.get("bot_id") \
                or ev.get("subtype") or not t["enabled"]:
            return JSONResponse({"ok": True})
        if seen := data.get("event_id"):
            if slack.seen_event(t["id"], seen):
                return JSONResponse({"ok": True, "duplicate": True})
        fields = slack.mention_fields(ev, data.get("team_id") or "")
        if not fields["text"]:
            return JSONResponse({"ok": True})
        ctx = {"deliver": "slack_thread", "channel": ev.get("channel"), "thread_ts": ev.get("thread_ts") or ev.get("ts"),
               "base_url": base} if (t["tokens"] or {}).get("bot_token") else None
        r = await core.fire(t, slack.build_input(t["config"], fields), context=ctx)
        if r.status == "started":
            return JSONResponse({"ok": True, "run_id": r.run_id})
        return _fired_response(r, request)

    form = {k: v[0] for k, v in parse_qs(body.decode(errors="replace")).items()}
    if form.get("ssl_check"):
        return Response(status_code=200)
    if not t["enabled"]:
        return _ephemeral("This Forge trigger is turned off.")
    fields = slack.slash_fields(form)
    if not fields["text"]:
        cmd = fields["command"] or "/forge"
        return _ephemeral(f"Add a task after the command, for example `{cmd} summarize the open issues`.")
    ctx = {"deliver": "slack_response_url", "response_url": form.get("response_url") or "", "base_url": base}
    r = await core.fire(t, slack.build_input(t["config"], fields), context=ctx)
    if r.status == "rate_limited":
        return _fired_response(r, request)
    if r.status != "started":
        return _ephemeral(f"Forge could not start the run: {r.detail}")
    url = core.run_url(base, r.run_id)  # type: ignore[arg-type]
    return _ephemeral(f"Working on it. <{url}|View run {r.run_id}>")
