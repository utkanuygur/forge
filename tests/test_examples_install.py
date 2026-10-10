"""One click example agents: discovery in a checkout and in a simulated frozen bundle, staging, and the
/v1/examples endpoints (install creates the agent, cases and prompts; 409 on an existing slug; replace syncs)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forge import db, examples

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "demo"
KEYS = ["data-analyst", "docs-writer", "log-triage", "repo-maintainer"]
LOCAL = ("127.0.0.1", 5000)
REMOTE = ("203.0.113.9", 5000)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_DEFAULT_MODEL", "fake:hello")
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    monkeypatch.delenv("FORGE_EXAMPLES_DIR", raising=False)


@pytest.fixture
def client():
    from forge.server.app import app

    with TestClient(app, client=LOCAL) as c:
        yield c


# ---------------------------------------------------------------- discovery


def test_dev_layout_resolves_to_the_repo_demo_dir():
    assert examples.demo_dir() == DEMO
    found = examples.discover()
    assert [ex["key"] for ex in found] == KEYS
    rm = examples.get("repo-maintainer")
    assert rm["seed_dir"] == (DEMO / "acme-orders").resolve()
    assert rm["cases"] == json.loads((DEMO / "evals.json").read_text())


def test_discover_rejects_unknown_keys():
    with pytest.raises(examples.ExampleError):
        examples.discover(["nope"])
    with pytest.raises(examples.ExampleError):
        examples.get("nope")


def test_stage_ships_only_what_installs_need(tmp_path):
    files = examples.stage(tmp_path / "demo")
    assert files and not any("solutions/" in f or f.endswith(("build.py", "prompt_checks.json", "common.py"))
                             for f in files)
    assert not any("__pycache__" in f or ".pytest_cache" in f for f in files)
    assert {"repo-maintainer.json", "evals.json", "examples/log-triage/agent.json",
            "examples/log-triage/evals.json"} <= set(files)
    assert any(f.startswith("acme-orders/") for f in files)


def test_frozen_layout_reads_from_meipass(tmp_path, monkeypatch):
    """Simulates the PyInstaller onedir bundle: data under <_MEIPASS>/demo, the checkout out of reach."""
    bundle = tmp_path / "Forge.app" / "Contents" / "Resources" / "forge-server" / "_internal"
    examples.stage(bundle / "demo")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert examples.demo_dir() == bundle / "demo"
    frozen = examples.discover()
    assert [ex["key"] for ex in frozen] == KEYS
    for ex in frozen:
        assert ex["dir"].is_relative_to(bundle)
        assert ex["seed_dir"].is_relative_to(bundle.resolve())
        dev = examples.resolve(DEMO / "examples" / ex["key"])
        assert ex["spec"] == dev["spec"] and ex["cases"] == dev["cases"]
        assert examples.read_seed_files(ex["seed_dir"]) == examples.read_seed_files(dev["seed_dir"])


def test_frozen_bundle_without_examples_lists_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert examples.discover() == []


# ---------------------------------------------------------------- endpoints


def test_list_examples(client):
    items = client.get("/v1/examples").json()
    assert [i["key"] for i in items] == KEYS
    lt = next(i for i in items if i["key"] == "log-triage")
    assert lt == {"key": "log-triage", "name": "Log triage", "description": lt["description"],
                  "agent_slug": "log-triage", "installed": False, "eval_cases": 4, "suggested_prompts": 3,
                  "needs_network": False}
    assert lt["description"]


def test_install_creates_agent_with_seed_cases_and_prompts(client):
    r = client.post("/v1/examples/log-triage/install", json={})
    assert r.status_code == 200, r.text
    agent = r.json()
    ex = examples.get("log-triage")
    assert agent["slug"] == "log-triage" and agent["created"] is True and agent["api_key"].startswith("fk_")
    assert agent["prod_version"] == 1 and agent["suggested_prompts"] == ex["spec"]["suggested_prompts"]
    v = client.get("/v1/agents/log-triage/versions/prod").json()
    assert v["model"] == "fake:hello" and v["system_prompt"] == ex["spec"]["system_prompt"]
    assert v["seed_files"] == examples.read_seed_files(ex["seed_dir"]) and "logs/nginx/access.log" in v["seed_files"]
    cases = client.get("/v1/agents/log-triage/evals").json()
    assert sorted(c["name"] for c in cases) == sorted(c["name"] for c in ex["cases"])
    assert next(i for i in client.get("/v1/examples").json() if i["key"] == "log-triage")["installed"] is True


def test_install_with_model_override(client):
    model = "openai_compat:http://localhost:11434/v1|qwen3:4b-instruct"
    r = client.post("/v1/examples/data-analyst/install", json={"model": model})
    assert r.status_code == 200, r.text
    assert client.get("/v1/agents/data-analyst/versions/prod").json()["model"] == model


def test_existing_slug_is_409_unless_replace(client):
    assert client.post("/v1/examples/docs-writer/install").status_code == 200
    r = client.post("/v1/examples/docs-writer/install")
    assert r.status_code == 409 and "already exists" in r.json()["detail"]

    # Someone edits the agent; replace restores prompts and cases and adds a version only when needed.
    agent = db.get_agent_by_slug("docs-writer")
    client.patch("/v1/agents/docs-writer", json={"suggested_prompts": []})
    first = db.list_eval_cases(agent["id"])[0]
    client.delete(f"/v1/evals/{first['id']}")
    same = client.post("/v1/examples/docs-writer/install?replace=1").json()
    assert same["created"] is False and "api_key" not in same and same["prod_version"] == 1
    assert len(same["versions"]) == 1 and same["eval_cases_synced"]["added"] == 1
    assert same["suggested_prompts"] == examples.get("docs-writer")["spec"]["suggested_prompts"]

    changed = client.post("/v1/examples/docs-writer/install?replace=1", json={"model": "fake:lessons"}).json()
    assert changed["installed_version"] == 2 and changed["prod_version"] == 1  # like load_examples: not promoted
    promoted = client.post("/v1/examples/docs-writer/install?replace=1&promote=1",
                           json={"model": "fake:other"}).json()
    assert promoted["installed_version"] == 3 and promoted["prod_version"] == 3
    assert promoted["eval_cases_synced"] == {"added": 0, "replaced": 0, "kept": 5}


def test_install_unknown_is_404(client):
    assert client.post("/v1/examples/nope/install").status_code == 404


def test_install_all_skips_installed(client):
    client.post("/v1/examples/log-triage/install")
    r = client.post("/v1/examples/install-all", json={"model": "fake:hello"}).json()
    assert sorted(a["slug"] for a in r["installed"]) == ["data-analyst", "docs-writer", "repo-maintainer"]
    assert r["skipped"] == ["log-triage"]
    assert all(i["installed"] for i in client.get("/v1/examples").json())
    again = client.post("/v1/examples/install-all").json()
    assert again["installed"] == [] and len(again["skipped"]) == 4


def test_examples_are_builder_only():
    from forge.server.app import app

    with TestClient(app, client=REMOTE) as c:
        assert c.get("/v1/examples").status_code == 401
        assert c.post("/v1/examples/log-triage/install").status_code == 401
        assert c.post("/v1/examples/install-all").status_code == 401
