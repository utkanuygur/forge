"""Unit tests for assist (forge.assist): shared agent context, registry, engine, the "task" kind and the routes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from forge import assist, db
from forge.assist import context as C, engine, registry
from forge.assist.kinds import task as T
from forge.providers import AnthropicProvider, make_provider
from forge.types import Message, ModelResponse, ToolSpec

LOCAL = ("127.0.0.1", 5000)
REMOTE = ("203.0.113.9", 5000)
FAKE = T.FAKE_TASKS


class ScriptedProvider:
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.calls: list[dict] = []

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                       temperature: float | None = None) -> ModelResponse:
        self.calls.append({"system": system, "user": messages[0].text})
        return ModelResponse(text=self.texts.pop(0), tokens_in=7, tokens_out=3)


def reply(prompt: str = "Please add a GET /orders/{order_id}/total endpoint in src/acme/app.py with tests.",
          title: str = "Order total endpoint") -> str:
    return json.dumps({"result": {"title": title, "prompt": prompt}, "rationale": "Exercises a small feature."})


def task_req(**kw) -> registry.AssistRequest:
    agent = kw.pop("agent", {"slug": "a", "name": "A", "description": "d"})
    version = {"system_prompt": "You fix bugs.", "tools": ["bash"], "network": False,
               "seed_files": kw.pop("seed_files", {"README.md": "# hi"})}
    return registry.AssistRequest(kind="task", agent=agent, version=version, recent_inputs=kw.pop("recent", []),
                                  avoid=kw.pop("avoid", []), generation=kw.pop("generation", 0),
                                  input=kw.pop("input", {}), secret_names=kw.pop("secrets", []))


def ctx_of(**kw) -> dict:
    r = task_req(**kw)
    return C.agent_context(r.agent, r.version, recent_inputs=r.recent_inputs, secret_names=r.secret_names)


# ---------------------------------------------------------------- shared agent context


def test_file_listing_is_capped_at_200_and_counts_the_rest():
    seeds = {f"data/f{i:04d}.txt": "x" for i in range(450)} | {"README.md": "# Repo"}
    c = ctx_of(seed_files=seeds)
    assert len(c["files"]) == 200 and c["total_files"] == 451 and "README.md" in c["files"]
    assert "(251 more files not listed)" in C.render_agent_context(c)


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
    c = ctx_of(seed_files=seeds)
    paths = [p for p, _ in c["excerpts"]]
    assert paths[0] == "README.md" and paths[1] == "src/pkg/app.py"
    assert "data/sales.csv" in paths and "logs/app.log" in paths
    assert "package-lock.json" not in paths and "empty.py" not in paths
    assert sum(len(e) + len(p) + 30 for p, e in c["excerpts"]) <= C.MAX_EXCERPT_TOTAL
    ex = dict(c["excerpts"])
    assert ex["data/sales.csv"].startswith("columns: region, total, week\nrows: 3")
    assert ex["logs/app.log"].count("\n") == 11  # first 12 lines of a log
    assert all(len(e) <= C.MAX_EXCERPT_CHARS for e in ex.values())
    assert C.file_score("tests/test_app.py", big) < C.file_score("src/pkg/app.py", big)


def test_long_system_prompt_is_condensed_keeping_start_and_end():
    text = "START " + "word " * 3000 + " END"
    out = C.condense_system_prompt(text)
    assert len(out) < C.MAX_SYSTEM_PROMPT_CHARS + 60 and out.startswith("START") and out.endswith("END")
    assert "chars omitted" in out and C.condense_system_prompt("short") == "short"


def test_context_dedupes_recent_inputs_suggestions_and_secret_names_only():
    c = ctx_of(recent=["Fix the bug"] * 5 + [f"task {i}" for i in range(20)])
    assert c["recent_inputs"][0] == "Fix the bug" and len(c["recent_inputs"]) == 10
    agent = {"name": "A", "suggested_prompts": json.dumps([{"title": "t", "prompt": "Do X"}])}
    c2 = C.agent_context(agent, {"seed_files": {}}, recent_inputs=[], secret_names=["TOKEN"])
    text = C.render_agent_context(c2)
    assert "- Do X" in text and "configured secrets: TOKEN" in text and "workspace starts empty" in text
    assert "network: off" in text


def test_near_duplicate_detection_uses_normalized_text():
    a = "Add a /refunds endpoint to src/acme/app.py, with tests."
    assert C.near_duplicate("add a refunds endpoint to src acme app py with tests", [a]) == a
    assert C.near_duplicate("Tests, with src/acme/app.py to endpoint /refunds a add", [a]) == a  # reordered
    assert C.near_duplicate("Explain how pricing rounds totals in src/acme/pricing.py.", [a]) is None
    assert C.similarity("", "x") == 0.0


# ---------------------------------------------------------------- registry


def _no_unsupported(schema) -> None:
    if isinstance(schema, dict):
        assert not {"minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"} & set(schema)
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False
            assert set(schema["required"]) == set(schema["properties"])
        for v in schema.values():
            _no_unsupported(v)


def test_registry_validates_registrations_and_envelopes_every_kind():
    assert "task" in assist.kinds() and assist.get("task") is T.SPEC and assist.get("nope") is None
    for k in assist.kinds():
        _no_unsupported(registry.envelope_schema(assist.get(k)))
    noop = lambda r: registry.Prepared("x")  # noqa: E731
    ok = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    with pytest.raises(ValueError, match="already registered"):
        registry.register("task", noop, "other", ok, lambda r, d: [])
    with pytest.raises(ValueError, match="lowercase"):
        registry.register("Bad-Kind", noop, "other", ok, lambda r, d: [])
    with pytest.raises(ValueError, match="additionalProperties"):
        registry.register("loose", noop, "other", {"type": "object", "properties": {}}, lambda r, d: [])
    with pytest.raises(ValueError, match="own system prompt"):
        registry.register("copycat", noop, T.SYSTEM, ok, lambda r, d: [])
    assert registry.by_system_prompt(T.SYSTEM) is T.SPEC


@pytest.fixture
def echo_kind():
    """A test-only kind without an agent and without share access: result {"text"} must contain "ok"."""
    spec = registry.register(
        "echo_test", lambda r: registry.Prepared(f"say ok about {r.input.get('topic', '?')}", {"n": 1}, {"d": 1}),
        "ECHO TEST SYSTEM", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                             "additionalProperties": False},
        lambda res, data: [] if "ok" in res["text"] else ["text must contain ok"], share_allowed=False,
        needs_agent=False, fake=lambda user: {"result": {"text": f"ok: {user}"}, "rationale": "r"})
    yield spec
    registry.unregister("echo_test")


# ---------------------------------------------------------------- engine


async def test_engine_retries_once_listing_problems_then_succeeds(echo_kind):
    p = ScriptedProvider(json.dumps({"result": {"text": "no"}, "rationale": ""}), '{"result": {"text": "ok"}}')
    out = await engine.run_assist(echo_kind, registry.Prepared("go"), p)
    assert out == {"result": {"text": "ok"}, "rationale": ""} and len(p.calls) == 2
    assert "<problems>\n- text must contain ok" in p.calls[1]["user"] and p.calls[0]["user"] == "go"


async def test_engine_second_invalid_reply_raises(echo_kind):
    p = ScriptedProvider("not json", json.dumps({"result": {}, "rationale": ""}))
    with pytest.raises(engine.AssistError) as e:
        await engine.run_assist(echo_kind, registry.Prepared("go"), p)
    assert e.value.errors == ["result is missing text"] and len(p.calls) == 2


def test_redact_replaces_secret_values_everywhere():
    out = engine.redact({"result": {"a": "use sk-123456 now", "b": ["sk-123456"]}, "rationale": "x"}, ["sk-123456"])
    assert "sk-123456" not in json.dumps(out) and out["result"]["b"] == ["[REDACTED]"]


async def test_anthropic_uses_structured_output_without_temperature():
    seen: list[dict] = []

    class Messages:
        async def create(self, **kw):
            seen.append(kw)
            return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=40, output_tokens=9),
                                   content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=reply())])

    g = engine.structured(T.SPEC, AnthropicProvider("claude-opus-5-5", client=SimpleNamespace(messages=Messages())))
    out = await engine.run_assist(T.SPEC, T.build_context(task_req()), g)
    assert out["result"]["title"] == "Order total endpoint"
    [kw] = seen
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": registry.envelope_schema(T.SPEC)}}
    assert "temperature" not in kw and [m["role"] for m in kw["messages"]] == ["user"]


# ---------------------------------------------------------------- kind: task


def test_task_context_adds_avoid_current_variety_and_hint():
    p = T.build_context(task_req(avoid=["A"], input={"current": "B", "hint": "about pricing"}, generation=7))
    assert "<avoid>\n- A\n- B\n</avoid>" in p.text and "<hint>\nabout pricing\n</hint>" in p.text
    assert T.VARIETY[7 % len(T.VARIETY)] in p.text and p.diagnostics["variety"] == T.VARIETY[1]
    assert p.data["avoid"] == ["A", "B"] and p.diagnostics["excerpts"] == ["README.md"]


def test_task_validation_flags_empty_long_and_duplicate_prompts():
    data = {"recent_inputs": ["Please add a GET /orders/{order_id}/total endpoint in src/acme/app.py with tests."],
            "avoid": []}
    assert "prompt is empty" in T.validate({"title": "t", "prompt": ""}, data)
    assert any("under 1200" in e for e in T.validate({"title": "t", "prompt": "x" * 1300}, data))
    assert any("title has" in e for e in T.validate({"title": "t" * 101, "prompt": "Explain it."}, data))
    assert any("nearly duplicates" in e for e in T.validate(json.loads(reply())["result"], data))
    assert T.validate({"title": "t", "prompt": "Explain the pricing rounding."}, data) == []


async def test_task_retry_on_duplicate_then_succeeds():
    avoid = ["Please add a GET /orders/{order_id}/total endpoint in src/acme/app.py with tests."]
    p = ScriptedProvider(reply(), reply("Why does test_pricing fail on Python 3.13? Find out and fix it."))
    out = await engine.run_assist(T.SPEC, T.build_context(task_req(avoid=avoid)), p)
    assert out["result"]["prompt"].startswith("Why does") and len(p.calls) == 2
    assert all(x["system"] == T.SYSTEM for x in p.calls) and "nearly duplicates" in p.calls[1]["user"]


async def test_fake_grader_returns_fixed_task_and_skips_ones_already_in_the_request():
    fake = make_provider("fake:grader")
    first = await engine.run_assist(T.SPEC, T.build_context(task_req()), fake)
    assert first["result"]["prompt"] == FAKE[0]["prompt"]
    second = await engine.run_assist(T.SPEC, T.build_context(task_req(recent=[FAKE[0]["prompt"]])), fake)
    assert second["result"]["prompt"] == FAKE[1]["prompt"]
    judged = await fake.complete("You are a strict grader", [Message(role="user", text="x")], [])
    assert json.loads(judged.text)["passed"] is True


def test_rate_limiter_sliding_window():
    from forge.server.assist import RateLimiter

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
    from forge.server import assist as route
    from forge.server.app import app

    route.share_limiter.reset()
    with TestClient(app, client=LOCAL) as c:
        c.post("/v1/agents", json={"slug": "s", "name": "S", "system_prompt": "You help.", "model": "fake:echo",
                                   "seed_files": {"README.md": "# S"}})
        yield c
    route.share_limiter.reset()


def test_assist_task_builder_gets_diagnostics_and_call_is_metered(client):
    r = client.post("/v1/assist/task", json={"slug": "s"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["result"] == {"title": FAKE[0]["title"], "prompt": FAKE[0]["prompt"]} and d["rationale"]
    assert d["kind"] == "task" and d["version"] == 1 and d["diagnostics"]["excerpts"] == ["README.md"]
    with db.connect() as c:
        rows = c.execute("SELECT kind, model, tokens_in FROM grader_calls WHERE agent_id = ?",
                         (db.get_agent_by_slug("s")["id"],)).fetchall()
    assert [tuple(x) for x in rows] == [("assist:task", "fake:grader", 120)]
    cost = client.get("/v1/agents/s/metrics?days=1").json()["cost"]["assist"]
    assert cost["calls"] == 1 and cost["kinds"] == {"task": 1}
    # input.current is avoided like `avoid`; the variety advances with the generation count
    d2 = client.post("/v1/assist/task", json={"slug": "s", "input": {"current": d["result"]["prompt"]}}).json()
    assert d2["result"]["prompt"] == FAKE[1]["prompt"]
    assert d2["diagnostics"]["variety"] != d["diagnostics"]["variety"]
    assert client.post("/v1/assist/task", json={"slug": "s", "version": 9}).status_code == 404
    assert client.post("/v1/assist/task", json={"slug": "nope"}).status_code == 404
    assert client.post("/v1/assist/task", json={}).status_code == 400  # needs an agent
    assert client.post("/v1/assist/unknown", json={"slug": "s"}).status_code == 404


def test_sample_task_alias_is_flat(client):
    d = client.post("/v1/agents/s/sample-task", json={"avoid": [FAKE[0]["prompt"]]}).json()
    assert d["prompt"] == FAKE[1]["prompt"] and d["title"] == FAKE[1]["title"] and d["version"] == 1
    assert d["context"]["excerpts"] == ["README.md"] and d["variety"]


def test_share_token_rate_limited_across_kinds_and_sees_no_config(client, echo_kind):
    token = client.get("/v1/agents/s").json()["share_token"]
    share = {"X-Share-Token": token}
    with TestClient(client.app, client=REMOTE) as remote:
        assert remote.post("/v1/assist/task", json={"slug": "s"}).status_code == 401
        assert remote.post("/v1/assist/echo_test", json={"slug": "s"}, headers=share).status_code == 401  # kind
        assert remote.post("/v1/assist/echo_test", json={}).status_code == 401  # no agent: localhost only
        for i in range(5):
            r = remote.post("/v1/assist/task", json={"slug": "s", "version": 1}, headers=share)
            assert r.status_code == 200, r.text
            assert set(r.json()) == {"result", "rationale"}
        for i in range(5):
            r = remote.post("/v1/agents/s/sample-task", json={}, headers=share)
            assert r.status_code == 200 and set(r.json()) == {"title", "prompt", "rationale"}
        r = remote.post(f"/v1/assist/task?share={token}", json={"slug": "s"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "Too many" in r.json()["detail"]
    assert client.post("/v1/assist/task", json={"slug": "s"}).status_code == 200  # builders are not limited


def test_kind_without_agent_runs_locally_unmetered(client, echo_kind):
    r = client.post("/v1/assist/echo_test", json={"input": {"topic": "cats"}})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["text"] == "ok: say ok about cats" and r.json()["version"] is None
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM grader_calls").fetchone()[0] == 0


def test_secret_values_never_reach_context_or_results(client, monkeypatch):
    client.put("/v1/agents/s/secrets", json={"API_TOKEN": "sk-very-secret"})
    from forge.server import evals

    p = ScriptedProvider(reply("Use sk-very-secret to call the API from src/app.py and report the status."))
    monkeypatch.setattr(evals, "grader", lambda: p)
    d = client.post("/v1/assist/task", json={"slug": "s"}).json()
    assert "sk-very-secret" not in json.dumps(d) and "[REDACTED]" in d["result"]["prompt"]
    assert "sk-very-secret" not in p.calls[0]["user"] and "configured secrets: API_TOKEN" in p.calls[0]["user"]


def test_502_when_model_fails_twice(client, monkeypatch):
    from forge.server import evals

    monkeypatch.setattr(evals, "grader", lambda: ScriptedProvider("nope", "still nope"))
    r = client.post("/v1/agents/s/sample-task", json={})
    assert r.status_code == 502 and "could not write a valid task suggestion" in r.json()["detail"]
