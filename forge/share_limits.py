"""Share link limits and visitor ids for share and API conversations (CONTRACT.md "Share and API conversations").

Every limit is enforced on the server before a share turn starts and answers 429 with `Retry-After`:

- `session_turns`: turns in one conversation (default 20)
- `sessions_per_visitor`: conversations a visitor may have open (not expired) at once (default 3)
- `daily_turns`: share turns per share link per UTC day, one shot share runs included (default 200)
- `concurrent_turns`: share turns running at the same time per share link (default 2)

Global defaults are settings (forge/settings.py) backed by environment variables (FORGE_SHARE_SESSION_TURNS, FORGE_SHARE_SESSIONS_PER_VISITOR,
FORGE_SHARE_DAILY_TURNS, FORGE_SHARE_CONCURRENT_TURNS); an agent may override each one (`agents.share_limits`).
Conversations idle for FORGE_SESSION_IDLE_HOURS (default 24) expire and become read only.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import secrets
import time
from typing import Any

from forge import db, settings
from forge.types import ACTIVE_STATUSES

LIMIT_KEYS = ("session_turns", "sessions_per_visitor", "daily_turns", "concurrent_turns")
DEFAULTS = {"session_turns": 20, "sessions_per_visitor": 3, "daily_turns": 200, "concurrent_turns": 2}
ENV = {"session_turns": "FORGE_SHARE_SESSION_TURNS", "sessions_per_visitor": "FORGE_SHARE_SESSIONS_PER_VISITOR",
       "daily_turns": "FORGE_SHARE_DAILY_TURNS", "concurrent_turns": "FORGE_SHARE_CONCURRENT_TURNS"}
MAX_LIMIT = 100_000
IDLE_HOURS_DEFAULT = 24.0
CONCURRENT_RETRY_S = 15
VISITOR_PREFIX = "vis_"

_LABELS = {"session_turns": ("Share conversation turns", "Turns allowed in one share link conversation."),
           "sessions_per_visitor": ("Share conversations per visitor",
                                    "Open conversations one share link visitor may have at once."),
           "daily_turns": ("Share turns per day", "Share turns each share link may run per UTC day."),
           "concurrent_turns": ("Concurrent share turns", "Share turns each share link may run at the same time.")}
for _k in LIMIT_KEYS:
    settings.register_setting(key=f"share_{_k}", env=ENV[_k], type="int", default=DEFAULTS[_k], min=1,
                              max=MAX_LIMIT, group="Sharing", label=_LABELS[_k][0], description=_LABELS[_k][1])
settings.register_setting(key="session_idle_hours", env="FORGE_SESSION_IDLE_HOURS", type="float",
                          default=IDLE_HOURS_DEFAULT, min=0, max=100000, group="Sharing",
                          label="Share conversation idle hours",
                          description="Hours without activity after which share and Slack conversations become read only.")


def now() -> float:
    """The clock every limit reads; tests replace it to cross day boundaries and expiry."""
    return time.time()


# ---------------------------------------------------------------- configuration


def global_defaults() -> dict[str, int]:
    return {k: max(1, min(MAX_LIMIT, int(settings.get(f"share_{k}")))) for k in LIMIT_KEYS}


def idle_seconds() -> float:
    return max(float(settings.get("session_idle_hours")), 0.0) * 3600


def overrides(agent: dict) -> dict[str, int | None]:
    raw = agent.get("share_limits") or {}
    return {k: (int(raw[k]) if isinstance(raw.get(k), (int, float)) and not isinstance(raw.get(k), bool) else None)
            for k in LIMIT_KEYS}


def effective(agent: dict) -> dict[str, int]:
    base = global_defaults()
    return {k: v if v is not None else base[k] for k, v in overrides(agent).items()}


# ---------------------------------------------------------------- errors


class LimitExceeded(Exception):
    """A share limit was reached. Rendered as 429 `{detail, limit, retry_after}` with a Retry-After header."""

    def __init__(self, limit: str, detail: str, retry_after: float) -> None:
        super().__init__(detail)
        self.limit, self.detail, self.retry_after = limit, detail, max(0, int(retry_after + 0.999))


def day_start(t: float) -> float:
    d = _dt.datetime.fromtimestamp(t, _dt.UTC)
    return float(calendar.timegm((d.year, d.month, d.day, 0, 0, 0)))


# ---------------------------------------------------------------- visitors


def new_visitor(agent_id: str) -> str:
    vid = VISITOR_PREFIX + secrets.token_urlsafe(24)
    db.create_visitor(vid, agent_id)
    return vid


def known_visitor(agent_id: str, visitor_id: str | None) -> bool:
    return bool(visitor_id) and db.get_visitor(visitor_id or "", agent_id) is not None


# ---------------------------------------------------------------- sessions


def expires_at(session: dict) -> float:
    return float(session["updated_at"]) + idle_seconds()


def is_expired(session: dict, t: float | None = None) -> bool:
    return (t if t is not None else now()) >= expires_at(session)


def check_new_session(agent: dict, visitor_id: str) -> None:
    """Raises LimitExceeded when the visitor already has `sessions_per_visitor` open conversations."""
    limit = effective(agent)["sessions_per_visitor"]
    t = now()
    open_ = [s for s in db.visitor_sessions(agent["id"], visitor_id) if not is_expired(s, t)]
    if len(open_) >= limit:
        soonest = min(expires_at(s) for s in open_)
        raise LimitExceeded(
            "sessions_per_visitor",
            f"You already have {limit} open conversations with this agent. Continue one of them, or try again "
            "after one has been idle for a while.", soonest - t)


def check_turn(agent: dict, session: dict | None) -> None:
    """Checks every per turn share limit, in the order a visitor can act on them.

    Callers run this and `RunManager.start_run` (which creates the run row before its first await) without an
    await in between, so on the single server event loop two turns can never both pass the daily and
    concurrency checks."""
    lim = effective(agent)
    t = now()
    if session is not None and db.session_turn_count(session["id"]) >= lim["session_turns"]:
        raise LimitExceeded(
            "session_turns",
            f"This conversation reached its limit of {lim['session_turns']} messages. Start a new conversation "
            "to keep going.", 0)
    start = day_start(t)
    if db.count_share_runs(agent["id"], since=start) >= lim["daily_turns"]:
        raise LimitExceeded("daily_turns", "You've reached today's limit for this link. Try again tomorrow.",
                            start + 86400 - t)
    if db.count_share_runs(agent["id"], statuses=ACTIVE_STATUSES) >= lim["concurrent_turns"]:
        raise LimitExceeded("concurrent_turns",
                            "This link is busy with other conversations right now. Try again in a moment.",
                            CONCURRENT_RETRY_S)


def limits_view(agent: dict) -> dict[str, Any]:
    return {"share_limits": overrides(agent), "share_limits_effective": effective(agent),
            "share_limit_defaults": global_defaults(), "session_idle_hours": idle_seconds() / 3600}
