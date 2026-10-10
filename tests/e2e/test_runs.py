"""End-to-end run tests against a real server with Docker sandboxes and fake models."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.e2e.helpers import assert_gapless, events, make_tarball, poll, read_sse, tar_members, wait_run

pytestmark = pytest.mark.e2e


def bearer(agent: dict) -> dict:
    return {"Authorization": f"Bearer {agent['api_key']}"}


def test_run_lifecycle_with_api_key_produces_trace_diff_and_artifacts(api, make_agent):
    """FR2, FR5, FR6, FR7: an API-key run with wait=true writes and runs hello.py in the sandbox, returns the
    final Run, records a gapless trace and exposes the change as a diff and a workspace tarball."""
    agent = make_agent("hello")
    hdr = bearer(agent)
    r = api.post(f"/agents/{agent['slug']}/runs", json={"input": "write hello.py and run it", "wait": True},
                 headers=hdr)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "succeeded", run
    assert run["source"] == "api" and run["version_n"] == 1 and run["error"] is None
    assert run["output"] == "Created and ran hello.py." and run["turns"] == 3
    assert run["has_artifacts"] and run["ended_at"] >= run["started_at"] >= run["created_at"]

    evs = events(api, run["id"], headers=hdr)
    assert_gapless(evs)
    assert [e["type"] for e in evs] == ["run_started", "tool_call", "tool_result", "tool_call", "tool_result",
                                        "assistant", "run_finished"]
    bash_result = evs[4]["payload"]
    assert bash_result["name"] == "bash" and "hello from forge" in bash_result["output"]
    assert not bash_result["is_error"]
    assert evs[-1]["payload"]["status"] == "succeeded"

    diff = api.get(f"/runs/{run['id']}/diff", headers=hdr)
    assert diff.status_code == 200 and "hello.py" in diff.text and "+print('hello from forge')" in diff.text
    art = api.get(f"/runs/{run['id']}/artifacts", headers=hdr)
    assert art.status_code == 200 and art.headers["content-type"] == "application/gzip"
    assert tar_members(art.content)["hello.py"] == b"print('hello from forge')\n"


def test_async_run_streams_sse_until_end_and_resumes_after_seq(api, make_agent):
    """FR7, NFR3: an async run returns immediately; /events streams the live trace and ends with `event: end`;
    resuming with ?after=N replays only events with seq > N."""
    agent = make_agent("sse")
    run = api.post(f"/agents/{agent['slug']}/runs", json={"input": "go"}).json()
    assert run["status"] in ("queued", "running")

    msgs = read_sse(api, run["id"])
    body = [m for m in msgs if m["event"] != "end"]
    assert [int(m["id"]) for m in body] == list(range(len(body)))
    assert [m["event"] for m in body] == [m["data"]["type"] for m in body]
    assert body[0]["event"] == "run_started" and body[-1]["event"] == "run_finished"
    assert wait_run(api, run["id"])["status"] == "succeeded"

    after = 2
    later = read_sse(api, run["id"], after=after)
    later_body = [m for m in later if m["event"] != "end"]
    assert [int(m["id"]) for m in later_body] == list(range(after + 1, len(body)))
    assert later[-1]["event"] == "end"
    # Last-Event-ID header resumes too (what EventSource sends on reconnect)
    with api.stream("GET", f"/runs/{run['id']}/events", headers={"Last-Event-ID": str(len(body) - 2)}) as s:
        text = "".join(s.iter_text())
    assert "event: run_finished" in text and "event: run_started" not in text and "event: end" in text


def test_json_seed_files_and_multipart_uploads_land_in_workspace(api, make_agent):
    """FR6: JSON seed_files, a multipart single file and a multipart .tar.gz are all placed in the sandbox
    workspace (seen in the exported workspace tarball) alongside the version's own seed files."""
    agent = make_agent("files", model="fake:echo", seed_files={"VERSION_SEED.md": "from version"})
    slug = agent["slug"]

    run = api.post(f"/agents/{slug}/runs",
                   json={"input": "look", "wait": True, "seed_files": {"data/in.txt": "json seed"}}).json()
    files = tar_members(api.get(f"/runs/{run['id']}/artifacts").content)
    assert files["data/in.txt"] == b"json seed" and files["VERSION_SEED.md"] == b"from version"

    run = api.post(f"/agents/{slug}/runs", data={"input": "look", "wait": "true"},
                   files={"files": ("notes.md", b"# uploaded notes\n", "text/markdown")}).json()
    assert run["status"] == "succeeded"
    files = tar_members(api.get(f"/runs/{run['id']}/artifacts").content)
    assert files["notes.md"] == b"# uploaded notes\n"

    tgz = make_tarball({"repo/README.md": b"readme", "repo/src/app.py": b"print(1)\n"})
    run = api.post(f"/agents/{slug}/runs", data={"input": "look", "wait": "true"},
                   files={"files": ("repo.tar.gz", tgz, "application/gzip")}).json()
    files = tar_members(api.get(f"/runs/{run['id']}/artifacts").content)
    assert files["README.md"] == b"readme" and files["src/app.py"] == b"print(1)\n"  # top dir stripped
    assert "repo.tar.gz" not in files


def test_secret_values_are_redacted_from_events_and_output(api, make_agent):
    """NFR6, FR3: a secret's value never appears in stored events or run output; [REDACTED] does. The hello
    scenario's script prints a string equal to the secret, so the bash tool result would carry it; the echo
    scenario repeats its input, so the model output would carry it."""
    secret = "hello from forge"
    agent = make_agent("secret")
    slug = agent["slug"]
    assert api.put(f"/agents/{slug}/secrets", json={"GREETING": secret}).json() == {"names": ["GREETING"]}
    run = api.post(f"/agents/{slug}/runs", json={"input": "go", "wait": True}).json()
    assert run["status"] == "succeeded"
    raw = api.get(f"/runs/{run['id']}/events.json").text
    assert secret not in raw and "[REDACTED]" in raw
    bash = [e for e in json.loads(raw) if e["type"] == "tool_result" and e["payload"]["name"] == "bash"][0]
    assert "[REDACTED]" in bash["payload"]["output"]
    assert secret not in json.dumps(read_sse(api, run["id"]))  # the live stream is redacted too

    echo = make_agent("secret-echo", model="fake:echo")
    api.put(f"/agents/{echo['slug']}/secrets", json={"TOKEN": "sk-test-0123456789"})
    run = api.post(f"/agents/{echo['slug']}/runs", json={"input": "my key is sk-test-0123456789", "wait": True}).json()
    assert run["output"] == "echo: my key is [REDACTED]"
    raw = api.get(f"/runs/{run['id']}/events.json").text
    assert "sk-test-0123456789" not in raw and raw.count("[REDACTED]") >= 2  # run_started input + output


def test_turn_limit_ends_run_with_timeout_status(api, make_agent):
    """NFR5: a model that calls bash forever is stopped at the turn limit with status timeout."""
    agent = make_agent("loop", model="fake:loop")
    run = api.post(f"/agents/{agent['slug']}/runs", json={"input": "spin", "wait": True}).json()
    assert run["status"] == "timeout" and "turn limit" in (run["error"] or "")
    assert run["turns"] == 30
    evs = events(api, run["id"])
    assert_gapless(evs)
    assert evs[-1]["payload"]["status"] == "timeout"


def test_model_failure_ends_run_failed_with_error_event(api, make_agent):
    """Reliability (NFR5 family): a model API failure fails the run cleanly with an error event and a
    run_finished event, and the sandbox is still cleaned up (checked by the session leak assertion)."""
    agent = make_agent("broken", model="fake:error")
    run = api.post(f"/agents/{agent['slug']}/runs", json={"input": "x", "wait": True}).json()
    assert run["status"] == "failed" and "fake model failure" in run["error"]
    evs = events(api, run["id"])
    assert_gapless(evs)
    errs = [e for e in evs if e["type"] == "error"]
    assert len(errs) == 1 and "fake model failure" in errs[0]["payload"]["message"]
    assert evs[-1]["payload"]["status"] == "failed"


class _Receiver(BaseHTTPRequestHandler):
    bodies: list[dict] = []

    def do_POST(self) -> None:
        n = int(self.headers.get("content-length") or 0)
        type(self).bodies.append(json.loads(self.rfile.read(n)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def webhook_receiver():
    _Receiver.bodies = []
    srv = HTTPServer(("127.0.0.1", 0), _Receiver)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/hook", _Receiver.bodies
    srv.shutdown()
    srv.server_close()


def test_webhook_receives_final_run_json(api, make_agent, webhook_receiver):
    """FR14: an async run with callback_url POSTs the final Run JSON to the callback when it finishes."""
    url, bodies = webhook_receiver
    agent = make_agent("hook")
    run = api.post(f"/agents/{agent['slug']}/runs", json={"input": "go", "callback_url": url},
                   headers=bearer(agent)).json()
    assert run["status"] in ("queued", "running")
    poll(lambda: bodies or None, timeout=60, what="webhook delivery")
    body = bodies[0]
    assert body["id"] == run["id"] and body["status"] == "succeeded"
    assert body["output"] == "Created and ran hello.py." and body["turns"] == 3 and body["ended_at"]
    final = api.get(f"/runs/{run['id']}").json()
    assert {k: body[k] for k in ("status", "output", "turns", "version_n")} == \
        {k: final[k] for k in ("status", "output", "turns", "version_n")}
    assert len(bodies) == 1


def test_unauthenticated_remote_client_is_rejected(api, make_agent):
    """FR5, NFR1: without a key or share token a non-local client cannot start or read runs. The server sits
    behind uvicorn's proxy-header support, so X-Forwarded-For from 127.0.0.1 makes the request look remote."""
    agent = make_agent("auth")
    remote = {"X-Forwarded-For": "203.0.113.9"}
    assert api.post(f"/agents/{agent['slug']}/runs", json={"input": "x"}, headers=remote).status_code == 401
    assert api.get("/agents", headers=remote).status_code == 401
    bad = {**remote, "Authorization": "Bearer fk_wrong"}
    assert api.post(f"/agents/{agent['slug']}/runs", json={"input": "x"}, headers=bad).status_code == 401
    ok = api.get(f"/agents/{agent['slug']}/runs", headers={**remote, **bearer(agent)})
    assert ok.status_code == 200 and isinstance(ok.json(), list)
