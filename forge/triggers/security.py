"""Webhook signature verification (GitHub and Slack) and a per-trigger sliding window rate limiter."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from typing import Callable

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
