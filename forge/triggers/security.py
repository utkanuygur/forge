"""Webhook signature verification (GitHub and Slack) and a per-trigger sliding window rate limiter."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import deque
from typing import Callable
from urllib.parse import urlsplit

SLACK_MAX_AGE = 300  # seconds; Slack recommends rejecting requests older than five minutes


def new_secret() -> str:
    return "whsec_" + secrets.token_hex(24)


def _hex_hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def github_signature(secret: str, body: bytes) -> str:
    return "sha256=" + _hex_hmac(secret, body)


def verify_github(secret: str | None, body: bytes, header: str | None) -> bool:
    """`X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body>`, compared in constant time."""
    if not secret or not header:
        return False
    return hmac.compare_digest(github_signature(secret, body).encode(), header.strip().encode())


def slack_signature(secret: str, timestamp: str, body: bytes) -> str:
    return "v0=" + _hex_hmac(secret, b"v0:" + timestamp.encode() + b":" + body)


def verify_slack(secret: str | None, body: bytes, timestamp: str | None, header: str | None,
                 now: float | None = None, max_age: int = SLACK_MAX_AGE) -> tuple[bool, str]:
    """Slack v0 signing: HMAC-SHA256 of `v0:<timestamp>:<raw body>`; rejects stale timestamps (replay window)."""
    if not secret:
        return False, "this trigger has no signing secret"
    if not timestamp or not header:
        return False, "missing Slack signature headers"
    try:
        ts = int(timestamp)
    except ValueError:
        return False, "invalid Slack timestamp"
    if abs((time.time() if now is None else now) - ts) > max_age:
        return False, "Slack request timestamp is too old"
    if not hmac.compare_digest(slack_signature(secret, timestamp, body).encode(), header.strip().encode()):
        return False, "invalid Slack signature"
    return True, ""


class RateLimiter:
    """At most `limit` events per `window` seconds per key (sliding window, in memory)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.hits: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float = 3600) -> bool:
        now = self.clock()
        with self.lock:
            q = self.hits.setdefault(key, deque())
            while q and q[0] <= now - window:
                q.popleft()
            if limit <= 0 or len(q) >= limit:
                return False
            q.append(now)
            return True

    def retry_after(self, key: str, window: float = 3600) -> int:
        q = self.hits.get(key)
        if not q:
            return 0
        return max(1, int(q[0] + window - self.clock()) + 1)

    def reset(self, key: str | None = None) -> None:
        with self.lock:
            if key is None:
                self.hits.clear()
            else:
                self.hits.pop(key, None)


# ---------------------------------------------------------------- API base allowlist

DEFAULT_API_ORIGINS = {"github": "https://api.github.com", "slack": "https://slack.com"}


def _origin_parts(url: str) -> tuple[str, str, int | None] | None:
    try:
        u = urlsplit(url.strip())
        port = u.port
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
        return None
    return u.scheme, u.hostname.lower(), port


def operator_api_hosts() -> list[str]:
    """FORGE_TRIGGER_API_HOSTS: comma separated extra allowed API origins, e.g.
    `https://ghe.example.com,http://127.0.0.1:9000`. An entry without a scheme means https; an entry without a
    port allows any port of that scheme and host."""
    raw = os.environ.get("FORGE_TRIGGER_API_HOSTS") or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def api_base_allowed(kind: str, url: str) -> bool:
    """A per-trigger GitHub or Slack API base must be on https://api.github.com (GitHub) or https://slack.com
    (Slack), on the default port, unless the operator allows more origins with FORGE_TRIGGER_API_HOSTS."""
    got = _origin_parts(url)
    if got is None:
        return False
    scheme, host, port = got
    default = _origin_parts(DEFAULT_API_ORIGINS.get(kind, ""))
    if default and (scheme, host) == default[:2] and port in (None, 443):
        return True
    for entry in operator_api_hosts():
        want = _origin_parts(entry if "://" in entry else f"https://{entry}")
        if want is None:
            continue
        if (scheme, host) == want[:2] and (want[2] is None or port == want[2]):
            return True
    return False


def api_base_error(kind: str, url: str) -> str:
    return (f"api_base {url!r} is not allowed: use {DEFAULT_API_ORIGINS.get(kind, 'the default API')} "
            "or ask the operator to add the origin to FORGE_TRIGGER_API_HOSTS")
