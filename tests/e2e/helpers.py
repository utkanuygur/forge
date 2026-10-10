"""Polling and parsing helpers shared by the end-to-end tests."""

from __future__ import annotations

import io
import json
import tarfile
import time
from collections.abc import Callable
from typing import Any, TypeVar

import httpx

T = TypeVar("T")
TERMINAL = {"succeeded", "failed", "timeout"}


def poll(fn: Callable[[], T | None], *, timeout: float = 60, interval: float = 0.2, what: str = "condition") -> T:
    """Calls `fn` until it returns something other than None (or False) or `timeout` seconds pass."""
    end = time.monotonic() + timeout
    while True:
        value = fn()
        if value is not None and value is not False:
            return value
        if time.monotonic() > end:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        time.sleep(interval)


def wait_run(client: httpx.Client, run_id: str, timeout: float = 90, **kw: Any) -> dict:
    def done() -> dict | None:
        r = client.get(f"/runs/{run_id}", **kw).json()
        return r if r["status"] in TERMINAL else None

    return poll(done, timeout=timeout, what=f"run {run_id} to finish")


def wait_evals(client: httpx.Client, slug: str, version: int, n_results: int, timeout: float = 120) -> dict:
    def done() -> dict | None:
        x = client.get(f"/agents/{slug}/evals/results", params={"version": version}).json()
        return x if not x["running"] and len(x["results"]) >= n_results else None

    return poll(done, timeout=timeout, what=f"evals of {slug} v{version}")


def wait_improvement(client: httpx.Client, improvement_id: str, timeout: float = 150) -> dict:
    def done() -> dict | None:
        x = client.get(f"/improvements/{improvement_id}").json()
        return x if x["status"] != "running" else None

    return poll(done, timeout=timeout, what=f"improvement {improvement_id}")


def events(client: httpx.Client, run_id: str, **kw: Any) -> list[dict]:
    r = client.get(f"/runs/{run_id}/events.json", **kw)
    assert r.status_code == 200, r.text
    return r.json()


def assert_gapless(evs: list[dict]) -> None:
    """Seq numbers are 0, 1, 2, ... with no gaps, and the trace ends with run_finished."""
    assert [e["seq"] for e in evs] == list(range(len(evs))), [e["seq"] for e in evs]
    assert evs[0]["type"] == "run_started" and evs[-1]["type"] == "run_finished", [e["type"] for e in evs]
    assert sum(e["type"] == "run_finished" for e in evs) == 1


def tar_members(data: bytes) -> dict[str, bytes]:
    """Regular files in a .tar.gz keyed by normalized relative path (no leading ./)."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf:
            if m.isfile():
                name = m.name.removeprefix("./")
                if not name.startswith(".git/"):
                    out[name] = tf.extractfile(m).read()  # type: ignore[union-attr]
    return out


def make_tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def parse_sse(lines: list[str]) -> list[dict]:
    """Groups raw SSE lines into messages: {"event", "id", "data"}."""
    msgs, cur = [], {}
    for line in lines:
        if not line:
            if cur:
                msgs.append(cur)
            cur = {}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        cur[field] = cur[field] + "\n" + value if field == "data" and field in cur else value
    if cur:
        msgs.append(cur)
    return msgs


def read_sse(client: httpx.Client, run_id: str, timeout: float = 90, **params: Any) -> list[dict]:
    """Consumes /runs/{id}/events until `event: end`; returns messages with `data` parsed as JSON."""
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    with client.stream("GET", f"/runs/{run_id}/events", params=params, timeout=timeout) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            lines.append(line.rstrip("\r"))
            if line == "" and any(x.startswith("event: end") for x in lines[-4:]):
                break
            assert time.monotonic() < deadline, "SSE stream did not end in time"
    msgs = parse_sse(lines)
    assert msgs and msgs[-1].get("event") == "end", msgs[-3:]
    for m in msgs:
        if m.get("data") and m.get("event") != "end":
            m["data"] = json.loads(m["data"])
    return msgs
