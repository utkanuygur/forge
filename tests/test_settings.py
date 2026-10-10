"""Settings registry, precedence, API, model availability and eval early stop (CONTRACT.md "Settings")."""

from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import forge
from forge import models_available, pricing, settings
from forge.server.app import app  # imported first: it loads .env, which the `isolated` fixture then clears
from forge.types import Event, RunResult

REPO = Path(__file__).resolve().parents[1]
LOCAL = ("127.0.0.1", 5000)
REMOTE = ("203.0.113.9", 5000)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for s in settings.REGISTRY.values():
        monkeypatch.delenv(s.env, raising=False)
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("FORGE_DESKTOP", raising=False)
    monkeypatch.delenv("FORGE_DESKTOP_SECRETS", raising=False)
    settings.invalidate()
    (tmp_path / "data").mkdir()
    from forge import db

    with db.connect():  # creates forge.db so saved values are read
        pass
    yield
    settings.invalidate()


# ---------------------------------------------------------------- precedence and types


def test_precedence_env_over_saved_over_default(monkeypatch):
    assert settings.resolve("eval_repeats").value == 3
    assert settings.resolve("eval_repeats").source == "default"
    settings.save({"eval_repeats": 5})
    r = settings.resolve("eval_repeats")
    assert (r.value, r.source) == (5, "saved")
    monkeypatch.setenv("FORGE_EVAL_REPEATS", "7")
    r = settings.resolve("eval_repeats")
    assert (r.value, r.source) == (7, "env")
    view = settings.view("eval_repeats")
    assert view["source"] == "env" and view["editable"] is False
    monkeypatch.setenv("FORGE_EVAL_REPEATS", "")  # empty counts as unset
    assert settings.resolve("eval_repeats").source == "saved"
    settings.reset(["eval_repeats"])
    assert settings.resolve("eval_repeats").source == "default"


def test_invalid_env_keeps_env_source_and_reports_error(monkeypatch):
    monkeypatch.setenv("FORGE_EVAL_REPEATS", "lots")
    r = settings.resolve("eval_repeats")
    assert (r.value, r.source) == (3, "env") and "FORGE_EVAL_REPEATS" in r.error


@pytest.mark.parametrize("key,good,normalized,bad", [
    ("eval_repeats", "4", 4, 0),
    ("eval_repeats", 9, 9, 10),
    ("eval_repeats", 2.0, 2, True),
    ("sandbox_cpus", "1.5", 1.5, "many"),
    ("sandbox_cpus", 2, 2.0, 0),
    ("metrics_public", "on", True, "maybe"),
    ("metrics_public", False, False, 1),
    ("executor", "local", "local", "kubernetes"),
    ("default_model", " fake:echo ", "fake:echo", "claude-sonnet-5-5"),
    ("default_model", "openai_compat:http://localhost:11434/v1|qwen3:8b",
     "openai_compat:http://localhost:11434/v1|qwen3:8b", "openai_compat:qwen3"),
    ("grader_model", "", "", "gpt"),
    ("public_url", "https://forge.example.com/", "https://forge.example.com", "forge.example.com"),
    ("public_url", "", "", "ftp://x"),
    ("sandbox_memory", "512M", "512m", "lots"),
    ("sandbox_image", "my-image", "my-image", ""),
    ("prices", {"fake:hello": {"input": 1, "output": 2}}, {"fake:hello": [1.0, 2.0]}, {"x": [1]}),
    ("prices", {}, {}, {"x": [-1, 2]}),
    ("openai_endpoints", [{"name": "vLLM", "base_url": "https://gpu.example/v1/", "api_key_env": "VLLM_KEY"}],
     [{"name": "vLLM", "base_url": "https://gpu.example/v1", "api_key_env": "VLLM_KEY"}],
     [{"name": "a", "base_url": "http://x"}, {"name": "A", "base_url": "http://y"}]),
    ("openai_endpoints", [], [], [{"name": "bad key", "base_url": "http://x", "api_key_env": "lower-case"}]),
])
def test_validation_per_type(key, good, normalized, bad):
    ok, errors = settings.validate_update({key: good})
    assert errors == {} and ok[key] == normalized
    ok, errors = settings.validate_update({key: bad})
    assert key in errors and key not in ok and errors[key]


def test_secrets_env_only_and_never_shown(monkeypatch):
    v = settings.view("anthropic_api_key")
    assert v["is_set"] is False and "value" not in v and v["editable"] is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    monkeypatch.setenv("FORGE_DESKTOP_SECRETS", "ANTHROPIC_API_KEY")
    v = settings.view("anthropic_api_key")
    assert v["is_set"] and v["from_desktop"] and "sk-ant" not in json.dumps(v)
    _, errors = settings.validate_update({"anthropic_api_key": "sk-ant-x", "data_dir": "/tmp", "nope": 1})
    assert set(errors) == {"anthropic_api_key", "data_dir", "nope"}
    with pytest.raises(ValueError):
        settings.coerce(settings.setting("anthropic_api_key"), "x")


def test_live_vs_restart():
    settings.snapshot()
    assert settings.setting("eval_repeats").apply == "live"
    settings.save({"eval_repeats": 2, "max_concurrent_runs": 3})
    assert settings.get("eval_repeats") == 2  # live: read at use time
    assert settings.needs_restart("eval_repeats") is False
    assert settings.needs_restart("max_concurrent_runs") is True
    settings.snapshot()  # what a restart does
    assert settings.needs_restart("max_concurrent_runs") is False


def test_register_setting_for_other_branches():
    s = settings.register_setting(key="test_widget_limit", env="FORGE_TEST_WIDGET_LIMIT", type="int", default=5,
                                  group="Widgets", label="Widget limit", description="Widgets per page.", min=1)
    try:
        assert settings.get("test_widget_limit") == 5
        assert "Widgets" in settings.GROUPS
        assert any(g["name"] == "Widgets" for g in settings.groups())
        with pytest.raises(ValueError):
            settings.register_setting(key="test_widget_limit", env="FORGE_OTHER", type="int", default=1,
                                      group="Widgets", label="x", description="x")
        with pytest.raises(ValueError):
            settings.register_setting(key="other_widget", env="FORGE_TEST_WIDGET_LIMIT", type="int", default=1,
                                      group="Widgets", label="x", description="x")
    finally:
        settings.REGISTRY.pop(s.key)
        settings.GROUPS.remove("Widgets")


def test_env_example_matches_registry():
    """.env.example is generated from the registry; regenerate with `uv run python scripts/gen_env_example.py`."""
    assert (REPO / ".env.example").read_text() == settings.env_example()
    for s in settings.REGISTRY.values():
        assert s.env in settings.env_example()


def test_call_sites_read_settings_live(monkeypatch):
    from forge.server import evals, obslog

    settings.save({"default_model": "fake:echo", "prices": {"fake:echo": [3, 4]}, "log_json": "off",
                   "eval_concurrency": 2})
    assert evals.default_model() == "fake:echo" and evals.grader_model() == "fake:echo"
    assert evals.eval_concurrency() == 2
    assert pricing.price_for("fake:echo").input == 3.0
    assert obslog.enabled() is False
    monkeypatch.setenv("FORGE_PRICES", json.dumps({"fake:echo": [9, 9]}))  # environment wins
    assert pricing.price_for("fake:echo").input == 9.0


def test_openai_key_per_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "default-key")
    monkeypatch.setenv("VLLM_KEY", "vllm-key")
    settings.save({"openai_endpoints": [{"name": "vLLM", "base_url": "https://gpu.example/v1",
                                         "api_key_env": "VLLM_KEY"}]})
    assert settings.openai_key_for("https://gpu.example/v1/") == "vllm-key"
    assert settings.openai_key_for("http://localhost:11434/v1") == "default-key"


# ---------------------------------------------------------------- model availability


def mock_providers(routes: dict[str, Any]) -> None:
    """routes: URL prefix -> JSON body, an int status, or None for connection refused."""

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        for prefix, out in routes.items():
            if url.startswith(prefix):
                if out is None:
                    raise httpx.ConnectError("refused", request=req)
                if isinstance(out, int):
                    return httpx.Response(out, json={"error": {"message": "invalid x-api-key"}})
                return httpx.Response(200, json=out)
        raise httpx.ConnectError("refused", request=req)

    models_available.TRANSPORT = httpx.MockTransport(handler)
    models_available.clear_cache()


async def test_providers_reachable_down_and_cached(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    settings.save({"openai_endpoints": [{"name": "Ollama", "base_url": "http://localhost:11434/v1"},
                                        {"name": "GPU", "base_url": "https://gpu.example/v1"}]})
    mock_providers({"https://api.anthropic.com/v1/models": {"data": [{"id": "claude-sonnet-5-5"}]},
                    "http://localhost:11434/v1/models": {"data": [{"id": "qwen3:4b-instruct"}]},
                    "https://gpu.example": None})
    out = {p["name"]: p for p in await models_available.providers()}
    assert out["Claude"]["reachable"] and out["Claude"]["models"] == ["claude-sonnet-5-5"]
    assert out["Ollama"]["models"] == ["qwen3:4b-instruct"]
    assert out["GPU"]["reachable"] is False and out["GPU"]["error"] == "connection refused"
    # Everything goes down: results stay cached for 60 s, and refresh=True checks again.
    models_available.TRANSPORT = httpx.MockTransport(lambda r: httpx.Response(500))
    cached = {p["name"]: p for p in await models_available.providers()}
    assert cached["Claude"]["models"] == ["claude-sonnet-5-5"]
    fresh = {p["name"]: p for p in await models_available.providers(refresh=True)}
    assert fresh["Claude"]["reachable"] is False and fresh["Ollama"]["reachable"] is False
    mock_providers({"https://api.anthropic.com/v1/models": 401})
    out = {p["name"]: p for p in await models_available.providers()}
    assert out["Claude"]["reachable"] is False and "401" in out["Claude"]["error"]
    assert "invalid x-api-key" in out["Claude"]["error"]


async def test_model_warning():
    mock_providers({"http://localhost:11434/v1/models": {"data": [{"id": "qwen3:4b-instruct"}]}})
    w = await models_available.model_warning("openai_compat:http://localhost:11434/v1|qwen3:8b")
    assert w == ("Model 'qwen3:8b' is not available on http://localhost:11434/v1; available: qwen3:4b-instruct")
    assert await models_available.model_warning("openai_compat:http://localhost:11434/v1|qwen3:4b-instruct") is None
    assert await models_available.model_warning("fake:hello") is None
    assert "No Claude API key" in await models_available.model_warning("anthropic:claude-sonnet-5-5")
    w = await models_available.model_warning("openai_compat:http://localhost:9/v1|m")
    assert w.startswith("Could not reach http://localhost:9/v1")


# ---------------------------------------------------------------- API


@pytest.fixture
def client():
    with TestClient(app, client=LOCAL) as c:
        yield c


def test_settings_api(client, monkeypatch):
    monkeypatch.setenv("FORGE_SANDBOX_IMAGE", "from-env")
    body = client.get("/v1/settings").json()
    names = [g["name"] for g in body["groups"]]
    assert names[:7] == ["Models", "Sandbox", "Evaluations", "Server", "Observability", "Pricing", "Sharing"]
    flat = {s["key"]: s for g in body["groups"] for s in g["settings"]}
    assert flat["sandbox_image"]["source"] == "env" and flat["sandbox_image"]["value"] == "from-env"
    assert flat["default_model"]["source"] == "default" and flat["eval_repeats"]["apply"] == "live"
    assert "value" not in flat["anthropic_api_key"]
    assert body["info"]["data_dir"].endswith("data") and body["desktop"] is False

    r = client.patch("/v1/settings", json={"eval_repeats": 12, "sandbox_image": "x", "default_model": "fake:echo"})
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert set(errors) == {"eval_repeats", "sandbox_image"} and "at most 9" in errors["eval_repeats"]
    assert settings.resolve("default_model").source == "default"  # nothing saved when any key fails

    r = client.patch("/v1/settings", json={"eval_repeats": 5, "max_concurrent_runs": 2})
    flat = {s["key"]: s for g in r.json()["groups"] for s in g["settings"]}
    assert flat["eval_repeats"]["value"] == 5 and flat["eval_repeats"]["source"] == "saved"
    assert flat["max_concurrent_runs"]["needs_restart"] is True and r.json()["needs_restart"] is True
    r = client.post("/v1/settings/reset", json={"keys": ["eval_repeats", "max_concurrent_runs"]})
    flat = {s["key"]: s for g in r.json()["groups"] for s in g["settings"]}
    assert flat["eval_repeats"]["source"] == "default" and r.json()["needs_restart"] is False
    assert client.post("/v1/settings/reset", json={"keys": ["nope"]}).status_code == 422
    r = client.patch("/v1/settings", json={"eval_repeats": 4})
    r = client.patch("/v1/settings", json={"eval_repeats": None})  # null resets
    assert {s["key"]: s for g in r.json()["groups"] for s in g["settings"]}["eval_repeats"]["source"] == "default"

    with TestClient(client.app, client=REMOTE) as remote:
        assert remote.get("/v1/settings").status_code == 401
        assert remote.patch("/v1/settings", json={"eval_repeats": 2}).status_code == 401
        assert remote.get("/v1/models/available").status_code == 401


def test_models_available_endpoint_never_fails(client):
    mock_providers({})
    r = client.get("/v1/models/available?refresh=true")
    assert r.status_code == 200
    by = {p["id"]: p for p in r.json()["providers"]}
    assert by["anthropic"]["configured"] is False
    assert by["openai_compat:http://localhost:11434/v1"]["reachable"] is False


def test_default_model_setting_and_version_warning(client):
    mock_providers({"http://localhost:11434/v1/models": {"data": [{"id": "qwen3:4b-instruct"}]}})
    client.patch("/v1/settings", json={"default_model": "fake:echo"})
    r = client.post("/v1/agents", json={"slug": "s1", "name": "S", "system_prompt": "p"})
    assert r.status_code == 200 and "warning" not in r.json()
    assert client.get("/v1/agents/s1/versions/prod").json()["model"] == "fake:echo"
    r = client.post("/v1/agents/s1/versions", json={"model": "openai_compat:http://localhost:11434/v1|qwen3:8b"})
    assert r.status_code == 200 and r.json()["n"] == 2
    assert r.json()["warning"] == ("Model 'qwen3:8b' is not available on http://localhost:11434/v1; "
                                   "available: qwen3:4b-instruct")


# ---------------------------------------------------------------- eval early stop


class _Exec:
    async def start(self, seed, env): ...
    async def exec(self, cmd, timeout=120): ...
    async def export_workspace(self, a, d): ...
    async def stop(self): ...


def test_eval_stops_on_model_not_found(client, monkeypatch):
    """A version whose model is missing runs once, not k times per case, and reports a clear error."""
    calls: list[str] = []

    async def run_agent(version, input, *, run_id, emit, **kw):
        calls.append(input)
        await emit(Event(run_id=run_id, seq=0, ts=time.time(), type="run_started", payload={}))
        return RunResult(status="failed", error="model error: ModelNotFoundError: Model 'qwen3:8b' is not "
                                                "available on http://localhost:11434/v1 (model not found)")

    loop_mod = types.ModuleType("forge.loop")
    loop_mod.run_agent = run_agent  # type: ignore[attr-defined]
    ex_mod = types.ModuleType("forge.executors")
    ex_mod.make_executor = lambda kind, **kw: _Exec()  # type: ignore[attr-defined]
    prov_mod = types.ModuleType("forge.providers")
    prov_mod.make_provider = lambda model: object()  # type: ignore[attr-defined]
    for name, mod in (("loop", loop_mod), ("executors", ex_mod), ("providers", prov_mod)):
        monkeypatch.setitem(sys.modules, f"forge.{name}", mod)
        monkeypatch.setattr(forge, name, mod, raising=False)
    client.patch("/v1/settings", json={"eval_concurrency": 1})
    client.post("/v1/agents", json={"slug": "m1", "name": "M", "system_prompt": "p", "model": "fake:echo"})
    for i in range(3):
        client.post("/v1/agents/m1/evals", json={"name": f"c{i}", "input": f"case {i}", "checker_type": "shell",
                                                 "checker": "true"})
    client.post("/v1/agents/m1/evals/run", json={})
    end = time.time() + 10
    while time.time() < end:
        res = client.get("/v1/agents/m1/evals/results").json()
        if not res["running"] and res["error"]:
            break
        time.sleep(0.05)
    assert res["error"].startswith("Stopped evaluating v1 early: Model 'qwen3:8b' is not available")
    assert "Fix the model in Configure" in res["error"]
    assert len(calls) == 1  # not 3 cases x 3 repeats
    assert res["results"] == []
