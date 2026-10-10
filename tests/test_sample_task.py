"""Unit tests for sample task generation (forge.sample_task) and its route (forge.server.sample_task)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from forge import db, sample_task as st
from forge.providers import FAKE_SAMPLE_TASKS, AnthropicProvider, make_provider
from forge.types import Message, ModelResponse, ToolSpec

LOCAL = ("127.0.0.1", 5000)
REMOTE = ("203.0.113.9", 5000)


class ScriptedProvider:
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.calls: list[dict] = []

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                       temperature: float | None = None) -> ModelResponse:
        self.calls.append({"system": system, "user": messages[0].text})
        return ModelResponse(text=self.texts.pop(0), tokens_in=7, tokens_out=3)


def task(prompt: str = "Please add a GET /orders/{order_id}/total endpoint in src/acme/app.py with tests.",
         title: str = "Order total endpoint") -> str:
    return json.dumps({"title": title, "prompt": prompt, "rationale": "Exercises a small feature."})


def ctx(**kw) -> dict:
    agent = {"slug": "a", "name": "A", "description": "d"}
    version = {"system_prompt": "You fix bugs.", "tools": ["bash"], "network": False,
               "seed_files": kw.pop("seed_files", {"README.md": "# hi"})}
    return st.build_context(agent, version, recent_inputs=kw.pop("recent", []), avoid=kw.pop("avoid", []),
                            secret_names=[], generation=kw.pop("generation", 0))


# ---------------------------------------------------------------- context building


def test_file_listing_is_capped_at_200_and_counts_the_rest():
    seeds = {f"data/f{i:04d}.txt": "x" for i in range(450)} | {"README.md": "# Repo"}
    c = ctx(seed_files=seeds)
    assert len(c["files"]) == 200 and c["total_files"] == 451 and "README.md" in c["files"]
    assert "(251 more files not listed)" in st.render_request(c)


def test_excerpts_prefer_readme_modules_csv_headers_and_logs_and_stay_under_cap():
    big = "\n".join(f"line {i} " + "x" * 80 for i in range(400))
    seeds = {
        "README.md": "# Service\nRuns the order API.",
        "src/pkg/app.py": "from fastapi import FastAPI\napp = FastAPI()\n" + big,
        "data/sales.csv": "region,total,week\nwest,10,1\neast,12,1\nnorth,3,1\n",
        "logs/app.log": "\n".join(f"2026-01-01 ERROR boom {i}" for i in range(100)),
        "tests/test_app.py": big,
        "deep/a/b/c/d/util.py": big,
        "package-lock.json": big,
        "empty.py": "",
    } | {f"src/pkg/mod{i}.py": big for i in range(20)}
    c = ctx(seed_files=seeds)
    paths = [p for p, _ in c["excerpts"]]
    assert paths[0] == "README.md"
    assert paths.index("src/pkg/app.py") < paths.index("src/pkg/mod0.py") or "src/pkg/mod0.py" not in paths
    assert "data/sales.csv" in paths and "logs/app.log" in paths
    assert "package-lock.json" not in paths and "empty.py" not in paths
    total = sum(len(e) + len(p) + 30 for p, e in c["excerpts"])
    assert total <= st.MAX_EXCERPT_TOTAL
    ex = dict(c["excerpts"])
    assert ex["data/sales.csv"].startswith("columns: region, total, week\nrows: 3")
    assert ex["logs/app.log"].count("\n") == 11  # first 12 lines of a log
    assert all(len(e) <= st.MAX_EXCERPT_CHARS for e in ex.values())
    assert st.file_score("tests/test_app.py", big) < st.file_score("src/pkg/app.py", big)


def test_long_system_prompt_is_condensed_keeping_start_and_end():
    text = "START " + "word " * 3000 + " END"
    out = st.condense_system_prompt(text)
    assert len(out) < st.MAX_SYSTEM_PROMPT_CHARS + 60 and out.startswith("START") and out.endswith("END")
    assert "chars omitted" in out
    assert st.condense_system_prompt("short") == "short"


def test_context_dedupes_recent_inputs_caps_them_and_picks_variety_and_suggestions():
    recent = ["Fix the bug"] * 5 + [f"task {i}" for i in range(20)]
    c = ctx(recent=recent, generation=7)
    assert c["recent_inputs"][0] == "Fix the bug" and len(c["recent_inputs"]) == 10
    assert c["variety"] == st.VARIETY[7 % len(st.VARIETY)]
    agent = {"name": "A", "suggested_prompts": json.dumps([{"title": "t", "prompt": "Do X"}])}
    c2 = st.build_context(agent, {"seed_files": {}}, recent_inputs=[], avoid=[], secret_names=["TOKEN"],
                          generation=0)
    req = st.render_request(c2)
    assert "- Do X" in req and "configured secrets: TOKEN" in req and "workspace starts empty" in req
    assert "network: off" in req


# ---------------------------------------------------------------- duplicates and validation


def test_near_duplicate_detection_uses_normalized_text():
    a = "Add a /refunds endpoint to src/acme/app.py, with tests."
    assert st.near_duplicate("add a refunds endpoint to src acme app py with tests", [a]) == a
    assert st.near_duplicate("ADD a /refunds endpoint to src/acme/app.py with tests!!", [a]) == a
    assert st.near_duplicate("Tests, with src/acme/app.py to endpoint /refunds a add", [a]) == a  # reordered
    assert st.near_duplicate("Explain how pricing rounds totals in src/acme/pricing.py.", [a]) is None
    assert st.similarity("", "x") == 0.0


def test_validation_flags_empty_long_and_duplicate_prompts():
    c = ctx(recent=["Please add a GET /orders/{order_id}/total endpoint in src/acme/app.py with tests."])
    assert st.validate_task(None, c)
    assert "prompt is empty" in st.validate_task({"title": "t", "prompt": "", "rationale": ""}, c)
    long = st.validate_task({"title": "t", "prompt": "x" * 1300, "rationale": ""}, c)
    assert any("under 1200" in e for e in long)
    dup = st.validate_task(st.parse_task(task()), c)
    assert any("nearly duplicates" in e for e in dup)
    assert st.validate_task(st.parse_task(task("Explain the pricing rounding.")), c) == []


async def test_retry_once_lists_problems_then_succeeds():
    c = ctx(avoid=["Please add a GET /orders/{order_id}/total endpoint in src/acme/app.py with tests."])
    p = ScriptedProvider(task(), task("Why does test_pricing fail on Python 3.13? Find out and fix it."))
    out = await st.generate_sample_task(c, p)
    assert out["prompt"].startswith("Why does") and len(p.calls) == 2
    assert all(x["system"] == st.SAMPLE_TASK_SYSTEM for x in p.calls)
    assert "<problems>" in p.calls[1]["user"] and "nearly duplicates" in p.calls[1]["user"]
    assert "<problems>" not in p.calls[0]["user"]


async def test_second_invalid_reply_raises():
    p = ScriptedProvider("not json", json.dumps({"title": "", "prompt": "", "rationale": ""}))
    with pytest.raises(st.SampleTaskError) as e:
        await st.generate_sample_task(ctx(), p)
    assert "prompt is empty" in e.value.errors and len(p.calls) == 2


async def test_anthropic_uses_structured_output_without_temperature():
    seen: list[dict] = []

    class Messages:
        async def create(self, **kw):
            seen.append(kw)
            return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=40, output_tokens=9),
                                   content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=task())])

    g = st.StructuredGrader(AnthropicProvider("claude-opus-5-5", client=SimpleNamespace(messages=Messages())),
                            st.SAMPLE_TASK_SCHEMA)
    out = await st.generate_sample_task(ctx(), g)
    assert out["title"] == "Order total endpoint"
    [kw] = seen
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": st.SAMPLE_TASK_SCHEMA}}
    assert "temperature" not in kw and [m["role"] for m in kw["messages"]] == ["user"]


async def test_fake_grader_returns_fixed_task_and_skips_ones_already_in_the_request():
    fake = make_provider("fake:grader")
    first = await st.generate_sample_task(ctx(), fake)
    assert first == FAKE_SAMPLE_TASKS[0]
    second = await st.generate_sample_task(ctx(recent=[FAKE_SAMPLE_TASKS[0]["prompt"]]), fake)
    assert second == FAKE_SAMPLE_TASKS[1]
    judged = await fake.complete("You are a strict grader", [Message(role="user", text="x")], [])
    assert json.loads(judged.text)["passed"] is True


def test_rate_limiter_sliding_window():
    from forge.server.sample_task import RateLimiter

    rl = RateLimiter(2, 100.0)
    assert rl.check("t", now=0) is None and rl.check("t", now=1) is None
    assert rl.check("t", now=2) == pytest.approx(98.0)
    assert rl.check("other", now=2) is None
    assert rl.check("t", now=100.5) is None  # the first hit expired


# ---------------------------------------------------------------- HTTP (no Docker)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_GRADER_MODEL", "fake:grader")
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    from forge.server import sample_task as route
    from forge.server.app import app

    route.share_limiter.reset()
    with TestClient(app, client=LOCAL) as c:
        c.post("/v1/agents", json={"slug": "s", "name": "S", "system_prompt": "You help.", "model": "fake:echo",
                                   "seed_files": {"README.md": "# S"}})
        yield c
    route.share_limiter.reset()


def test_endpoint_builder_gets_context_and_meters_the_call(client):
    r = client.post("/v1/agents/s/sample-task", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["prompt"] == FAKE_SAMPLE_TASKS[0]["prompt"] and d["version"] == 1
    assert d["context"]["excerpts"] == ["README.md"]
    agent = db.get_agent_by_slug("s")
    with db.connect() as c:
        rows = c.execute("SELECT kind, model, tokens_in FROM grader_calls WHERE agent_id = ?",
                         (agent["id"],)).fetchall()
    assert [tuple(x) for x in rows] == [("sample_task", "fake:grader", 120)]
    m = client.get("/v1/agents/s/metrics?days=1").json()
    assert m["cost"]["sample_tasks"]["calls"] == 1
    # avoid steers away from the first fake task; the variety advanced with the generation count
    d2 = client.post("/v1/agents/s/sample-task", json={"avoid": [d["prompt"]]}).json()
    assert d2["prompt"] == FAKE_SAMPLE_TASKS[1]["prompt"] and d2["variety"] != d["variety"]
    assert client.post("/v1/agents/s/sample-task", json={"version": 9}).status_code == 404
    assert client.post("/v1/agents/nope/sample-task", json={}).status_code == 404


def test_endpoint_share_token_is_rate_limited_and_sees_no_config(client):
    token = client.get("/v1/agents/s").json()["share_token"]
    with TestClient(client.app, client=REMOTE) as remote:
        assert remote.post("/v1/agents/s/sample-task", json={}).status_code == 401
        for _ in range(10):
            r = remote.post("/v1/agents/s/sample-task", json={}, headers={"X-Share-Token": token})
            assert r.status_code == 200, r.text
            assert set(r.json()) == {"title", "prompt", "rationale"}
        r = remote.post(f"/v1/agents/s/sample-task?share={token}", json={})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
        assert "Too many" in r.json()["detail"]


def test_endpoint_502_when_model_fails_twice(client, monkeypatch):
    from forge.server import evals

    monkeypatch.setattr(evals, "grader", lambda: ScriptedProvider("nope", "still nope"))
    r = client.post("/v1/agents/s/sample-task", json={})
    assert r.status_code == 502 and "could not write a valid sample task" in r.json()["detail"]
