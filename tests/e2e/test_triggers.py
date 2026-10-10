"""End-to-end trigger tests: a real server (own instance, with a file-backed fake clock for the scheduler),
Docker sandboxes and fake models. Slack's response_url and the GitHub REST API are local HTTP receivers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest

from forge.triggers.security import github_signature, slack_signature
from tests.conftest import docker_unavailable
from tests.e2e.conftest import REPO, _free_port
from tests.e2e.helpers import poll, wait_run

pytestmark = pytest.mark.e2e


class TriggerServer:
    def __init__(self, url: str, clock: Path, log: Path) -> None:
        self.url, self.clock, self.log = url, clock, log

    def set_clock(self, t: float | None) -> None:
        if t is None:
            self.clock.unlink(missing_ok=True)
        else:
            self.clock.write_text(repr(t))


@pytest.fixture(scope="module")
def tserver(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TriggerServer]:
    if why := docker_unavailable():
        pytest.skip(why)
    root = tmp_path_factory.mktemp("forge-triggers")
    log, clock = root / "server.log", root / "clock"
    port = _free_port()
    env = {k: v for k, v in os.environ.items()
           if (not k.startswith("FORGE_") or k == "FORGE_SANDBOX_IMAGE") and k != "ANTHROPIC_API_KEY"}
    env.update({
        "FORGE_DATA_DIR": str(root / "data"), "FORGE_EXECUTOR": "docker", "FORGE_DEFAULT_MODEL": "fake:echo",
        "FORGE_GRADER_MODEL": "fake:grader", "FORWARDED_ALLOW_IPS": "127.0.0.1", "PYTHONUNBUFFERED": "1",
        "FORGE_TEST_CLOCK_FILE": str(clock), "FORGE_SCHEDULER_INTERVAL": "0.2",
        "FORGE_SLACK_RESPONSE_URL_PREFIXES": "http://127.0.0.1:", "FORGE_TRIGGER_API_HOSTS": "http://127.0.0.1",
        "FORGE_TRIGGER_RETRY_BACKOFF": "0.1",
        "PYTHONPATH": os.pathsep.join(p for p in (str(REPO), os.environ.get("PYTHONPATH", "")) if p),
    })
    logf = log.open("w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "forge.server.app:app", "--host", "127.0.0.1",
                             "--port", str(port), "--log-level", "warning"], cwd=root, env=env, stdout=logf,
                            stderr=subprocess.STDOUT)
    srv = TriggerServer(f"http://127.0.0.1:{port}", clock, log)

    def healthy() -> bool | None:
        if proc.poll() is not None:
            raise AssertionError(f"server exited:\n{log.read_text()[-3000:]}")
        try:
            return httpx.get(f"{srv.url}/v1/templates", timeout=2).status_code == 200 or None
        except httpx.HTTPError:
            return None

    try:
        poll(healthy, timeout=30, interval=0.1, what="trigger server health")
        yield srv
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        logf.close()


@pytest.fixture(scope="module")
def tapi(tserver: TriggerServer) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=tserver.url + "/v1", timeout=60) as c:
        yield c


@pytest.fixture
def agent(tapi: httpx.Client) -> dict:
    slug = f"trg-{uuid.uuid4().hex[:6]}"
    r = tapi.post("/agents", json={"slug": slug, "name": "Triggered", "system_prompt": "x", "model": "fake:echo"})
    assert r.status_code == 200, r.text
    return r.json()


class _Recorder(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_POST(self) -> None:
        n = int(self.headers.get("content-length") or 0)
        type(self).calls.append({"path": self.path, "headers": dict(self.headers),
                                 "body": json.loads(self.rfile.read(n) or b"{}")})
        self.send_response(201)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def receiver() -> Iterator[tuple[str, list[dict]]]:
    _Recorder.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _Recorder.calls
    srv.shutdown()
    srv.server_close()


def github_post(c: httpx.Client, tid: str, event: str, payload: dict, secret: str | None, **headers) -> httpx.Response:
    body = json.dumps(payload).encode()
    h = {"X-GitHub-Event": event, "Content-Type": "application/json", **headers}
    if secret:
        h["X-Hub-Signature-256"] = github_signature(secret, body)
    return c.post(f"/hooks/github/{tid}", content=body, headers=h)


TUNNEL = {"cf-connecting-ip": "140.82.112.3", "x-forwarded-for": "140.82.112.3"}
ISSUE = {"action": "opened", "repository": {"full_name": "o/r"}, "sender": {"login": "alice", "type": "User"},
         "issue": {"number": 4, "title": "Crash on start", "body": "Trace here", "html_url": "https://g/i/4",
                   "user": {"login": "alice"}}}


def fires(c: httpx.Client, tid: str) -> list[dict]:
    return c.get(f"/triggers/{tid}/history").json()


def test_schedule_fires_once_per_slot_with_fake_clock(tapi, tserver, agent):
    """TRG6: the scheduler fires once in a slot (never twice while the clock sits in it), records missed slots
    older than 10 minutes without running them, then fires the next slot. Runs use source `schedule`."""
    now = time.time()
    tserver.set_clock(now)
    try:
        t = tapi.post(f"/agents/{agent['slug']}/triggers", json={"kind": "schedule", "config": {
            "cron": "0 * * * *", "timezone": "UTC", "input_template": "Hourly report for {date} {time}"}}).json()
        slot = (int(now) // 3600 + 1) * 3600
        tserver.set_clock(slot + 5)
        first = poll(lambda: [f for f in fires(tapi, t["id"]) if f["status"] == "started"] or None, timeout=20,
                     what="scheduled fire")
        run = wait_run(tapi, first[0]["run_id"])
        stamp = time.strftime("%Y-%m-%d %H:00", time.gmtime(slot))
        assert run["source"] == "schedule" and run["status"] == "succeeded"
        assert run["output"] == f"echo: Hourly report for {stamp}"
        time.sleep(1.5)  # about seven scheduler ticks inside the same slot
        assert [f["status"] for f in fires(tapi, t["id"])] == ["started"]

        tserver.set_clock(slot + 3 * 3600 + 20 * 60)  # three later slots, the newest 20 minutes late
        missed = poll(lambda: [f for f in fires(tapi, t["id"]) if f["status"] == "missed"] or None, timeout=20,
                      what="missed slot record")
        assert "Skipped 3 scheduled" in missed[0]["detail"]
        tserver.set_clock(slot + 4 * 3600 + 30)
        poll(lambda: len([f for f in fires(tapi, t["id"]) if f["status"] == "started"]) == 2 or None, timeout=20,
             what="second scheduled fire")
        time.sleep(1)
        history = fires(tapi, t["id"])
        assert [f["status"] for f in history] == ["started", "missed", "started"]
        assert wait_run(tapi, history[0]["run_id"])["status"] == "succeeded"  # no sandbox outlives the test
        tapi.patch(f"/triggers/{t['id']}", json={"enabled": False})
    finally:
        tserver.set_clock(None)


def test_signed_github_payload_starts_run_and_bad_signatures_get_401(tapi, agent):
    """TRG2, TRG4: through the tunnel (proxy headers) an unsigned or wrongly signed payload gets 401; a signed one
    gets 202 with the run id and the run sees the rendered template."""
    t = tapi.post(f"/agents/{agent['slug']}/triggers", json={"kind": "github", "config": {
        "events": ["issues"], "input_template": "Triage {repo}#{number} from {sender}: {title}"}}).json()
    assert github_post(tapi, t["id"], "issues", ISSUE, None, **TUNNEL).status_code == 401
    assert github_post(tapi, t["id"], "issues", ISSUE, "whsec_wrong", **TUNNEL).status_code == 401
    assert github_post(tapi, t["id"], "ping", {"zen": "hi"}, t["secret"], **TUNNEL).status_code == 200
    r = github_post(tapi, t["id"], "issues", ISSUE, t["secret"], **TUNNEL)
    assert r.status_code == 202, r.text
    run = wait_run(tapi, r.json()["run_id"])
    assert run["source"] == "github" and run["status"] == "succeeded"
    assert run["output"] == "echo: Triage o/r#4 from alice: Crash on start"
    assert github_post(tapi, t["id"], "pull_request", {"action": "opened"}, t["secret"]).status_code == 204


def test_issue_comment_command_filter(tapi, agent):
    """TRG2: only comments starting with the configured command start runs; {comment} is the rest of it."""
    t = tapi.post(f"/agents/{agent['slug']}/triggers", json={"kind": "github", "config": {
        "events": ["issue_comment"], "command": "/forge", "input_template": "{comment}"}}).json()

    def comment(body: str) -> dict:
        return {**ISSUE, "action": "created", "comment": {"body": body, "html_url": "https://g/c/1"}}

    assert github_post(tapi, t["id"], "issue_comment", comment("looks good"), t["secret"]).status_code == 204
    assert github_post(tapi, t["id"], "issue_comment", comment("cc /forge go"), t["secret"]).status_code == 204
    r = github_post(tapi, t["id"], "issue_comment", comment("/forge add a regression test"), t["secret"])
    assert r.status_code == 202
    assert wait_run(tapi, r.json()["run_id"])["output"] == "echo: add a regression test"
    assert [f["status"] for f in fires(tapi, t["id"])] == ["started"]


def test_github_comment_back_to_mock_api(tapi, agent, receiver):
    """TRG2: with a token the final output is posted as an issue comment through the (mock) GitHub REST API."""
    base, calls = receiver
    t = tapi.post(f"/agents/{agent['slug']}/triggers", json={"kind": "github", "token": "ghp_test_token", "config": {
        "events": ["issues"], "input_template": "{title}", "api_base": base}}).json()
    r = github_post(tapi, t["id"], "issues", ISSUE, t["secret"], **TUNNEL)
    run_id = r.json()["run_id"]
    poll(lambda: calls or None, timeout=60, what="GitHub comment")
    call = calls[0]
    assert call["path"] == "/repos/o/r/issues/4/comments"
    assert call["headers"]["Authorization"] == "Bearer ghp_test_token"
    assert call["body"]["body"].startswith("echo: Crash on start") and run_id in call["body"]["body"]
    hist = poll(lambda: (h := fires(tapi, t["id"])) and h[0]["delivery"] and h, timeout=10, what="delivery status")
    assert hist[0]["delivery"] == "delivered"
    assert "ghp_test_token" not in tapi.get(f"/agents/{agent['slug']}/triggers").text


def test_slack_slash_command_responds_at_once_and_posts_final_output(tapi, agent, receiver):
    """TRG3: a signed slash command gets an ephemeral "Working on it" reply with the run link within 3 seconds;
    the final output is then posted to response_url as mrkdwn. A stale timestamp gets 401."""
    base, calls = receiver
    t = tapi.post(f"/agents/{agent['slug']}/triggers", json={"kind": "slack", "signing_secret": "slack-sign",
                                                              "config": {"input_template": "**{text}**"}}).json()
    body = urlencode({"command": "/forge", "text": "weekly summary", "user_name": "bob",
                      "response_url": f"{base}/commands/T1/123"}).encode()

    def post(stamp: int) -> httpx.Response:
        s = str(stamp)
        return tapi.post(f"/hooks/slack/{t['id']}", content=body, headers={
            "Content-Type": "application/x-www-form-urlencoded", "X-Slack-Request-Timestamp": s,
            "X-Slack-Signature": slack_signature("slack-sign", s, body), **TUNNEL})

    assert post(int(time.time()) - 360).status_code == 401
    started = time.monotonic()
    r = post(int(time.time()))
    assert time.monotonic() - started < 3
    assert r.status_code == 200, r.text
    msg = r.json()
    assert msg["response_type"] == "ephemeral" and msg["text"].startswith("Working on it.")
    assert "/runs/r_" in msg["text"]
    poll(lambda: calls or None, timeout=60, what="response_url delivery")
    final = calls[0]
    assert final["path"] == "/commands/T1/123" and final["body"]["response_type"] == "in_channel"
    assert final["body"]["text"].startswith("echo: *weekly summary*")  # Markdown bold became Slack bold
    assert "|View the run>" in final["body"]["text"]
