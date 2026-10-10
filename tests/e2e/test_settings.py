"""End-to-end settings: live settings apply without a restart, restart settings say so, model warnings come
from a real (stub) OpenAI compatible server, and evals stop early on a missing model (CONTRACT.md "Settings")."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.e2e.helpers import poll, wait_evals

pytestmark = pytest.mark.e2e

STUB_PORT = 8203  # a tiny OpenAI compatible /v1/models endpoint


@pytest.fixture
def reset_settings(api) -> Iterator[None]:
    yield
    keys = [s["key"] for g in api.get("/settings").json()["groups"] for s in g["settings"] if s["source"] == "saved"]
    api.post("/settings/reset", json={"keys": keys})


def flat(body: dict) -> dict[str, dict]:
    return {s["key"]: s for g in body["groups"] for s in g["settings"]}


def test_live_setting_changes_eval_repeats_without_restart(api, make_agent, reset_settings):
    agent = make_agent("live-k", model="fake:echo")
    slug = agent["slug"]
    assert agent["eval_repeats"] == 3
    api.post(f"/agents/{slug}/evals", json={"name": "ok", "input": "hi", "checker_type": "shell", "checker": "true"})
    r = api.patch("/settings", json={"eval_repeats": 1})
    assert r.status_code == 200 and flat(r.json())["eval_repeats"]["source"] == "saved"
    assert flat(r.json())["eval_repeats"]["needs_restart"] is False
    assert api.get(f"/agents/{slug}").json()["eval_repeats"] == 1
    api.post(f"/agents/{slug}/evals/run", json={"version": 1})
    res = wait_evals(api, slug, 1, 1)
    assert res["repeats"] == 1 and (res["results"][0]["passes"], res["results"][0]["runs"]) == (1, 1)


def test_restart_setting_reports_needs_restart(api, reset_settings):
    body = api.patch("/settings", json={"max_concurrent_runs": 3}).json()
    s = flat(body)["max_concurrent_runs"]
    assert s["apply"] == "restart" and s["needs_restart"] is True and body["needs_restart"] is True
    # The environment owns these on this server, so they are read only.
    r = api.patch("/settings", json={"default_model": "fake:echo"})
    assert r.status_code == 422 and "FORGE_DEFAULT_MODEL" in r.json()["detail"]["errors"]["default_model"]
    body = api.post("/settings/reset", json={"keys": ["max_concurrent_runs"]}).json()
    assert body["needs_restart"] is False


@pytest.fixture
def stub_models() -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/v1/models":
                body = json.dumps({"object": "list", "data": [{"id": "qwen3:4b-instruct", "object": "model"}]})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{STUB_PORT}/v1"
    finally:
        srv.shutdown()
        srv.server_close()


def test_model_warning_on_version_save(api, make_agent, stub_models, reset_settings):
    api.patch("/settings", json={"openai_endpoints": [{"name": "Stub", "base_url": stub_models}]})
    # Only the stub: layers 1 to 5 make no calls to real model providers.
    r = api.get("/models/available", params={"refresh": True, "provider": "Stub"}).json()["providers"]
    assert [p["name"] for p in r] == ["Stub"]
    assert r[0]["reachable"] and r[0]["models"] == ["qwen3:4b-instruct"]

    slug = make_agent("warn", model="fake:echo")["slug"]
    r = api.post(f"/agents/{slug}/versions", json={"model": f"openai_compat:{stub_models}|qwen3:8b"})
    assert r.status_code == 200
    assert r.json()["warning"] == (f"Model 'qwen3:8b' is not available on {stub_models}; "
                                   "available: qwen3:4b-instruct")
    r = api.post(f"/agents/{slug}/versions", json={"model": f"openai_compat:{stub_models}|qwen3:4b-instruct"})
    assert "warning" not in r.json()


def test_eval_stops_early_when_model_is_missing(api, make_agent, reset_settings):
    api.patch("/settings", json={"eval_concurrency": 1})
    slug = make_agent("missing", model="fake:missing")["slug"]
    for i in range(3):
        api.post(f"/agents/{slug}/evals", json={"name": f"c{i}", "input": f"job {i}", "checker_type": "shell",
                                                "checker": "true"})
    api.post(f"/agents/{slug}/evals/run", json={"version": 1})

    def stopped() -> dict | None:
        x = api.get(f"/agents/{slug}/evals/results", params={"version": 1}).json()
        return x if not x["running"] and x["error"] else None

    res = poll(stopped, timeout=90, what="eval to stop")
    assert res["error"].startswith("Stopped evaluating v1 early: Model 'missing' is not available")
    assert res["results"] == []
    runs = [r for r in api.get(f"/agents/{slug}/runs").json() if r["source"] == "eval"]
    assert len(runs) == 1  # not 3 cases x 3 repeats
