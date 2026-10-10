"""Observability unit tests: aggregations, pricing, failure classification, timelines, Prometheus text,
loop timing fields and the SQL-backed search and metrics over a temporary database (OBS1 to OBS7)."""

from __future__ import annotations

import json
import time

import pytest

from forge import db, metrics, pricing
from forge.executors import LocalExecutor
from forge.loop import run_agent
from forge.providers import FakeProvider
from forge.types import AgentVersion, Event, ModelResponse, ToolCall

# ---------------------------------------------------------------- statistics (OBS1)


def test_percentile_interpolates_and_ignores_missing_values():
    """OBS1: p50 and p95 use linear interpolation; None values are skipped; empty input gives None."""
    assert metrics.percentile([], 50) is None
    assert metrics.percentile([None, None], 95) is None
    assert metrics.percentile([7], 95) == 7
    assert metrics.percentile([1, 2, 3, 4], 50) == 2.5
    assert metrics.percentile(list(range(1, 101)), 95) == pytest.approx(95.05)
    assert metrics.percentile([10, None, 30], 50) == 20


def _run(i: int, status: str = "succeeded", *, source: str = "api", dur: float = 1.0, turns: int = 2,
         created: float = 1000.0, cost: float | None = 0.01, **kw) -> dict:
    return {"id": f"r_{i}", "status": status, "source": source, "turns": turns, "tokens_in": 100, "tokens_out": 10,
            "created_at": created, "started_at": created, "ended_at": created + dur, "cost_usd": cost, **kw}


def test_health_success_rate_durations_turns_and_thumbs_up_rate():
    """OBS1: success rate counts only finished runs; thumbs up rate is +1 over rated items; cost sums known prices."""
    runs = [_run(1, dur=1), _run(2, dur=2), _run(3, "failed", dur=3), _run(4, "timeout", dur=4, turns=30),
            _run(5, "running", cost=None)]
    fb = [{"rating": 1}, {"rating": 1}, {"rating": -1}]
    h = metrics.health(runs, fb)
    assert (h["runs"], h["finished"], h["succeeded"], h["failed"], h["timeout"], h["in_progress"]) == (5, 4, 2, 1, 1, 1)
    assert h["success_rate"] == 0.5
    assert h["p50_ms"] == 2500 and h["p95_ms"] == pytest.approx(3850)
    assert h["avg_turns"] == (2 + 2 + 2 + 30) / 4
    assert h["thumbs_up_rate"] == pytest.approx(2 / 3) and h["feedback_count"] == 3
    assert h["cost_usd"] == pytest.approx(0.04) and h["unpriced_runs"] == 1
    empty = metrics.health([], [])
    assert empty["success_rate"] is None and empty["thumbs_up_rate"] is None and empty["p95_ms"] is None


def test_include_source_excludes_eval_runs_by_default():
    """OBS1: production metrics exclude eval runs unless include_eval is set."""
    runs = [_run(1), _run(2, source="eval"), _run(3, source="share")]
    assert [r["id"] for r in runs if metrics.include_source(r, False)] == ["r_1", "r_3"]
    assert len([r for r in runs if metrics.include_source(r, True)]) == 3


def test_period_delta_reports_absolute_and_relative_change():
    """OBS1: KPI deltas compare to the previous period; missing values give null changes."""
    assert metrics.delta(12, 10) == {"value": 12, "previous": 10, "change": 2, "change_pct": 0.2}
    assert metrics.delta(0.5, None)["change"] is None
    assert metrics.delta(3, 0)["change_pct"] is None
    k = metrics.kpis({"runs": 4, "success_rate": 0.75, "p95_ms": 100, "cost_usd": 1.0, "thumbs_up_rate": None},
                     {"runs": 2, "success_rate": 1.0, "p95_ms": 200, "cost_usd": 0.5, "thumbs_up_rate": 0.5})
    assert k["runs"]["change"] == 2 and k["success_rate"]["change"] == -0.25 and k["p95_ms"]["change_pct"] == -0.5
    assert k["thumbs_up_rate"]["change"] is None


def test_series_buckets_runs_by_status_aligned_to_local_midnight():
    """OBS1: daily buckets start at the client's local midnight and cover the whole period, empty days included."""
    day = metrics.DAY
    tz = -7 * 3600  # UTC-7
    base = 20 * day - tz  # local midnight
    runs = [_run(1, created=base + 100), _run(2, "failed", created=base + 200),
            _run(3, created=base + 2 * day + 5, cost=0.5)]
    s = metrics.series(runs, base, base + 2 * day + 10, day, tz)
    assert [b["t"] for b in s] == [base, base + day, base + 2 * day]
    assert [(b["runs"], b["succeeded"], b["failed"]) for b in s] == [(2, 1, 1), (0, 0, 0), (1, 1, 0)]
    assert s[0]["success_rate"] == 0.5 and s[1]["success_rate"] is None and s[2]["cost_usd"] == 0.5
    assert metrics.bucket_size(1) == metrics.HOUR and metrics.bucket_size(14) == day


# ---------------------------------------------------------------- pricing (OBS2)


def test_pricing_known_models_cost_per_million_tokens(monkeypatch):
    """OBS2: Anthropic list prices in USD per million input and output tokens."""
    monkeypatch.delenv("FORGE_PRICES", raising=False)
    assert pricing.cost("anthropic:claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)
    assert pricing.cost("anthropic:claude-haiku-5-5", 2_000_000, 0) == pytest.approx(0.2)
    assert pricing.cost("anthropic:claude-sonnet-4-6", 0, 100_000) == pytest.approx(1.5)
    assert pricing.price_for("anthropic:claude-sonnet-5-5").kind == "metered"


def test_pricing_self_hosted_models_cost_zero_with_note(monkeypatch):
    """OBS2: OpenAI compatible and fake models are self-hosted and not metered (cost 0)."""
    monkeypatch.delenv("FORGE_PRICES", raising=False)
    for m in ("openai_compat:http://localhost:11434/v1|qwen3:4b-instruct", "fake:hello"):
        p = pricing.price_for(m)
        assert p.kind == "self_hosted" and p.note == "self-hosted, not metered"
        assert pricing.cost(m, 5000, 5000) == 0


def test_pricing_unknown_anthropic_model_has_null_cost(monkeypatch):
    """OBS2: an unknown Anthropic model has no price, so its cost is null with an 'unknown price' note."""
    monkeypatch.delenv("FORGE_PRICES", raising=False)
    assert pricing.cost("anthropic:claude-mystery-9", 10, 10) is None
    assert pricing.price_for("anthropic:claude-mystery-9").note == "unknown price"


def test_pricing_overrides_from_env(monkeypatch):
    """OBS2: FORGE_PRICES overrides by full model string or bare Anthropic id; invalid entries are ignored."""
    monkeypatch.setenv("FORGE_PRICES", json.dumps({"fake:hello": [1, 2], "claude-opus-5-5": {"input": 10, "output": 30},
                                                   "fake:echo": "bad"}))
    assert pricing.cost("fake:hello", 1_000_000, 500_000) == pytest.approx(2.0)
    assert pricing.cost("anthropic:claude-opus-5-5", 1_000_000, 0) == pytest.approx(10.0)
    assert pricing.cost("fake:echo", 1_000_000, 0) == 0
    monkeypatch.setenv("FORGE_PRICES", "{not json")
    assert pricing.cost("fake:hello", 1_000_000, 0) == 0


# ---------------------------------------------------------------- failure classification (OBS4)


def test_classify_run_categories():
    """OBS4: timeouts, model errors, sandbox errors, tool errors, eval check failures and negative feedback."""
    cats = lambda r, **kw: [(f["category"], f["reason"], f["count"]) for f in metrics.classify_run(r, **kw)]  # noqa: E731
    assert cats({"status": "timeout", "error": "turn limit of 30 reached"}) == [("timeout", "turn limit of 30 reached", 1)]
    assert cats({"status": "failed", "error": "model error: RuntimeError: fake model failure"}) == [
        ("model_error", "RuntimeError: fake model failure", 1)]
    assert cats({"status": "failed", "error": "RuntimeError: docker run failed\nmore"}) == [
        ("sandbox_error", "RuntimeError: docker run failed", 1)]
    assert cats({"status": "succeeded"}) == []
    tool_errors = [{"name": "bash", "output": "exit_code: 2\n--- stdout ---\n\n--- stderr ---\nls: nope: No such file\n"},
                   {"name": "edit_file", "output": "`old` text not found"}]
    assert cats({"status": "succeeded"}, tool_errors=tool_errors) == [
        ("tool_errors", "bash: exit 2, ls: nope: No such file", 2)]
    assert cats({"status": "succeeded", "source": "eval"},
                eval_result={"passed": False, "reason": "exit 1. FAILED test_refund\ntraceback"}) == [
        ("eval_failed", "exit 1. FAILED test_refund", 1)]
    assert cats({"status": "succeeded"}, feedback=[{"rating": 1, "comment": "great"},
                                                   {"rating": -1, "comment": "Too terse\nreally"}]) == [
        ("negative_feedback", "Too terse", 1)]
    assert cats({"status": "succeeded"}, feedback=[{"rating": -1, "comment": ""}])[0][1] == "(no comment)"


def test_failure_breakdown_counts_runs_reasons_and_examples():
    """OBS4: top categories and reasons count runs (and occurrences) and keep example run ids, newest first."""
    items = [
        ({"id": "r_3"}, [{"category": "timeout", "reason": "turn limit", "count": 1},
                         {"category": "tool_errors", "reason": "bash: exit 1", "count": 4}]),
        ({"id": "r_2"}, [{"category": "timeout", "reason": "turn limit", "count": 1}]),
        ({"id": "r_1"}, []),
        ({"id": "r_0"}, [{"category": "negative_feedback", "reason": "wrong", "count": 1},
                         {"category": "negative_feedback", "reason": "wrong", "count": 1}]),
    ]
    b = metrics.failure_breakdown(items)
    assert b["runs"] == 3
    assert [(c["category"], c["runs"], c["count"]) for c in b["categories"]] == [
        ("timeout", 2, 2), ("tool_errors", 1, 4), ("negative_feedback", 1, 2)]
    assert b["categories"][0]["example_run_ids"] == ["r_3", "r_2"] and b["categories"][0]["label"] == "Timeout"
    assert b["reasons"][0] == {"category": "timeout", "label": "Timeout", "reason": "turn limit", "runs": 2,
                               "count": 2, "example_run_ids": ["r_3", "r_2"]}


# ---------------------------------------------------------------- tool stats (OBS6)


def test_tool_stats_from_events_with_duration_fallback():
    """OBS6: calls, errors, error rate, p50/p95 and total time per tool; old events use timestamps."""
    evs = [
        {"run_id": "r_1", "seq": 1, "ts": 10.0, "type": "tool_call", "payload": {"id": "a", "name": "bash", "args": {"cmd": "make test"}}},
        {"run_id": "r_1", "seq": 2, "ts": 10.5, "type": "tool_result", "payload": {"id": "a", "name": "bash", "is_error": True, "duration_ms": 400}},
        {"run_id": "r_1", "seq": 3, "ts": 11.0, "type": "tool_call", "payload": {"id": "b", "name": "bash", "args": {"cmd": "ls"}}},
        {"run_id": "r_1", "seq": 4, "ts": 11.1, "type": "tool_result", "payload": {"id": "b", "name": "bash", "is_error": False, "duration_ms": 100}},
        # an older trace without duration_ms
        {"run_id": "r_2", "seq": 1, "ts": 20.0, "type": "tool_call", "payload": {"id": "a", "name": "read_file", "args": {"path": "x.py"}}},
        {"run_id": "r_2", "seq": 2, "ts": 20.25, "type": "tool_result", "payload": {"id": "a", "name": "read_file", "is_error": False}},
    ]
    calls = metrics.tool_calls_from_events(evs)
    assert [(c["name"], c["duration_ms"], c["is_error"]) for c in calls] == [
        ("bash", 400, True), ("bash", 100, False), ("read_file", 250.0, False)]
    s = metrics.tool_stats(calls)
    bash = next(t for t in s["tools"] if t["name"] == "bash")
    assert (bash["calls"], bash["errors"], bash["error_rate"], bash["total_ms"]) == (2, 1, 0.5, 500)
    assert bash["p50_ms"] == 250 and bash["p95_ms"] == pytest.approx(385)
    assert [(c["run_id"], c["summary"]) for c in s["slowest"]] == [("r_1", "make test"), ("r_2", "x.py"), ("r_1", "ls")]


# ---------------------------------------------------------------- timeline (OBS3)


def _ev(seq: int, ts: float, typ: str, **payload) -> dict:
    return {"run_id": "r_1", "seq": seq, "ts": ts, "type": typ, "payload": payload}


def test_timeline_uses_recorded_durations_and_tokens():
    """OBS3: spans per model turn and tool call with durations and tokens on a shared axis; model vs tool time."""
    evs = [
        _ev(0, 100.0, "run_started"),
        _ev(1, 100.5, "tool_call", id="c0", name="write_file", args={"path": "a.py"}, turn=1, model_duration_ms=500,
            tokens_in=10, tokens_out=5),
        _ev(2, 100.6, "tool_result", id="c0", name="write_file", is_error=False, duration_ms=100),
        _ev(3, 101.6, "assistant", text="done", turn=2, duration_ms=1000, tokens_in=20, tokens_out=7),
        _ev(4, 101.7, "run_finished", status="succeeded"),
    ]
    tl = metrics.build_timeline({"id": "r_1", "status": "succeeded", "created_at": 99.0, "started_at": 99.5}, evs)
    assert not tl["estimated"]
    assert [(s["kind"], s["label"], s["start_ms"], s["duration_ms"]) for s in tl["spans"]] == [
        ("model", "Model turn 1", 0.0, 500.0), ("tool", "write_file", 500.0, 100.0), ("model", "Model turn 2", 600.0, 1000.0)]
    assert tl["spans"][0]["tokens_in"] == 10 and tl["spans"][2]["tokens_out"] == 7
    assert tl["spans"][1]["summary"] == "a.py"
    assert tl["model_ms"] == 1500 and tl["tool_ms"] == 100 and tl["total_ms"] == 1700 and tl["other_ms"] == 100
    assert tl["queue_ms"] == 500 and tl["turns"] == 2 and tl["tool_calls"] == 1


def test_timeline_falls_back_to_event_timestamps_for_old_runs():
    """OBS3: runs recorded before duration fields existed get estimated spans from event timestamps."""
    evs = [
        _ev(0, 100.0, "run_started"),
        _ev(1, 100.4, "tool_call", id="c0", name="bash", args={"cmd": "ls"}),
        _ev(2, 100.7, "tool_result", id="c0", name="bash", is_error=True),
        _ev(3, 101.0, "tool_call", id="c1", name="bash", args={"cmd": "pwd"}),
        _ev(4, 101.1, "tool_result", id="c1", name="bash", is_error=False),
        _ev(5, 101.5, "assistant", text="done"),
        _ev(6, 101.5, "run_finished", status="succeeded"),
    ]
    tl = metrics.build_timeline({"id": "r_1"}, evs)
    assert tl["estimated"]
    got = [(s["kind"], s["start_ms"], round(s["duration_ms"])) for s in tl["spans"]]
    assert got == [("model", 0.0, 400), ("tool", 400.0, 300), ("model", 700.0, 300), ("tool", 1000.0, 100),
                   ("model", 1100.0, 400)]
    assert all(s["estimated"] for s in tl["spans"]) and tl["spans"][1]["is_error"]
    assert tl["total_ms"] == 1500


def test_timeline_marks_failed_model_call():
    """OBS3: a model error becomes an error span using its duration_ms."""
    evs = [_ev(0, 10.0, "run_started"), _ev(1, 12.0, "error", message="model error: boom", duration_ms=2000),
           _ev(2, 12.0, "run_finished", status="failed")]
    tl = metrics.build_timeline({"id": "r_1"}, evs)
    assert [(s["label"], s["is_error"], s["duration_ms"]) for s in tl["spans"]] == [("Model call failed", True, 2000.0)]


# ---------------------------------------------------------------- prometheus (OBS7)


def test_prometheus_text_format_with_histogram_and_escaping():
    """OBS7: HELP and TYPE lines, escaped label values, cumulative histogram buckets with +Inf, sum and count."""
    text = metrics.format_prometheus([
        {"name": "forge_runs_total", "type": "counter", "help": "Runs.",
         "samples": [("forge_runs_total", {"agent": 'a"b', "status": "ok"}, 3)]},
        {"name": "forge_run_duration_seconds", "type": "histogram", "help": "Durations.",
         "samples": metrics.histogram_samples("forge_run_duration_seconds", {"agent": "x"}, [0.5, 3, 700],
                                              buckets=(1, 5))},
        {"name": "forge_active_runs", "type": "gauge", "help": "Active.", "samples": [("forge_active_runs", {}, 0)]},
    ])
    lines = text.splitlines()
    assert lines[:3] == ["# HELP forge_runs_total Runs.", "# TYPE forge_runs_total counter",
                         'forge_runs_total{agent="a\\"b",status="ok"} 3']
    assert 'forge_run_duration_seconds_bucket{agent="x",le="1"} 1' in lines
    assert 'forge_run_duration_seconds_bucket{agent="x",le="5"} 2' in lines
    assert 'forge_run_duration_seconds_bucket{agent="x",le="+Inf"} 3' in lines
    assert 'forge_run_duration_seconds_sum{agent="x"} 703.5' in lines
    assert 'forge_run_duration_seconds_count{agent="x"} 3' in lines
    assert "forge_active_runs 0" in lines and text.endswith("\n")


# ---------------------------------------------------------------- loop timing fields (OBS3)


async def test_loop_events_carry_additive_timing_and_token_fields(tmp_path):
    """OBS3: assistant events report per-turn duration and tokens; a tool-only turn reports them on its first
    tool_call; every tool_result has duration_ms; existing payload keys are unchanged."""
    ex = LocalExecutor(root=str(tmp_path / "ws"))
    await ex.start({}, {})
    events: list[Event] = []

    async def emit(e: Event) -> None:
        events.append(e)

    provider = FakeProvider([
        ModelResponse(tool_calls=[ToolCall(id="c0", name="write_file", args={"path": "a.txt", "content": "x"}),
                                  ToolCall(id="c1", name="bash", args={"cmd": "cat a.txt"})], tokens_in=11, tokens_out=3),
        ModelResponse(text="done", tokens_in=13, tokens_out=4),
    ])
    v = AgentVersion(id="v_t", agent_id="ag_t", n=1, system_prompt="s", model="fake:x")
    await run_agent(v, "go", run_id="r_t", provider=provider, executor=ex, emit=emit)
    await ex.stop()
    by_type = [(e.type, e.payload) for e in events]
    calls = [p for t, p in by_type if t == "tool_call"]
    assert calls[0]["turn"] == 1 and calls[0]["tokens_in"] == 11 and calls[0]["model_duration_ms"] >= 0
    assert "model_duration_ms" not in calls[1] and calls[1]["turn"] == 1
    assert all(p["duration_ms"] >= 0 and {"id", "name", "output", "is_error"} <= set(p)
               for t, p in by_type if t == "tool_result")
    assistant = next(p for t, p in by_type if t == "assistant")
    assert assistant["text"] == "done" and assistant["turn"] == 2 and assistant["tokens_out"] == 4
    assert assistant["duration_ms"] >= 0
    # run_finished keeps its keys; the only additions are feat/reliability's cache token totals.
    assert set(events[-1].payload) == {"status", "output", "turns", "tokens_in", "tokens_out",
                                       "cache_read_tokens", "cache_write_tokens"}


# ---------------------------------------------------------------- database backed (OBS1, OBS2, OBS5)


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_PRICES", json.dumps({"fake:hello": [1000, 2000]}))
    yield


def _seed_run(agent: dict, version: dict, *, status: str = "succeeded", source: str = "api", input: str = "task",
              output: str = "ok", dur: float = 1.0, tokens: tuple[int, int] = (100, 10), created: float | None = None,
              tool_error: bool = False, error: str | None = None) -> dict:
    r = db.create_run(agent["id"], version["id"], source, input)
    t = created if created is not None else time.time() - 60
    db.update_run(r["id"], status=status, output=output, error=error, turns=2, tokens_in=tokens[0],
                  tokens_out=tokens[1], created_at=t, started_at=t, ended_at=t + dur)
    db.append_event(r["id"], 0, t, "run_started", {})
    db.append_event(r["id"], 1, t + 0.1, "tool_call", {"id": "c0", "name": "bash", "args": {"cmd": "ls"}, "turn": 1,
                                                       "model_duration_ms": 100})
    db.append_event(r["id"], 2, t + 0.3, "tool_result", {"id": "c0", "name": "bash", "is_error": tool_error,
                                                         "output": "exit_code: 1\n--- stdout ---\n\n--- stderr ---\nboom\n"
                                                         if tool_error else "ok", "duration_ms": 200})
    db.append_event(r["id"], 3, t + dur, "run_finished", {"status": status})
    return db.get_run(r["id"])  # type: ignore[return-value]


def _agent(slug: str, model: str = "fake:hello") -> tuple[dict, dict]:
    a, _ = db.create_agent(slug, slug)
    v = db.create_version(a["id"], system_prompt="p", model=model)
    a = db.set_prod_version(a["id"], v["id"], "create")
    return a, v


def test_search_runs_filters_sorts_and_counts(tmpdb):
    """OBS5: status, version, source, rating, tool errors, text query, date range, sort and total count."""
    from forge.server.observability import search_runs

    a, v1 = _agent("alpha")
    v2 = db.create_version(a["id"], system_prompt="p2", model="fake:hello")
    now = time.time()
    r1 = _seed_run(a, v1, input="refund the order", dur=5, created=now - 300, tokens=(10, 1))
    r2 = _seed_run(a, v2, status="failed", output="could not find REFUND table", dur=1, created=now - 200,
                   tool_error=True, tokens=(1000, 100))
    r3 = _seed_run(a, v2, source="eval", dur=3, created=now - 100)
    db.create_feedback(r1["id"], 1)
    db.create_feedback(r2["id"], -1, "wrong")
    ids = lambda **kw: [r["id"] for r in search_runs(agent_id=a["id"], **kw)[0]]  # noqa: E731
    assert ids() == [r3["id"], r2["id"], r1["id"]]
    assert ids(status="failed") == [r2["id"]]
    assert ids(version="1") == [r1["id"]]
    assert ids(source="api,share") == [r2["id"], r1["id"]]
    assert ids(rating="up") == [r1["id"]] and ids(rating="down") == [r2["id"]] and ids(rating="none") == [r3["id"]]
    assert ids(has_tool_errors=True) == [r2["id"]] and r2["id"] not in ids(has_tool_errors=False)
    assert ids(q="refund") == [r2["id"], r1["id"]]  # input or output, case insensitive
    assert ids(q="100%") == []
    assert ids(since=now - 250, until=now - 150) == [r2["id"]]
    assert ids(sort="duration") == [r1["id"], r3["id"], r2["id"]]
    assert ids(sort="cost")[0] == r2["id"]
    rows, total = search_runs(agent_id=a["id"], limit=1, offset=1)
    assert total == 3 and [r["id"] for r in rows] == [r2["id"]] and rows[0]["agent_slug"] == "alpha"


def test_agent_metrics_versions_costs_failures_tools_and_promotions(tmpdb):
    """OBS1, OBS2, OBS4, OBS6: per version health excludes eval runs by default, cost uses FORGE_PRICES, the
    prod version reports deltas versus the previous prod, and promotions are recorded."""
    from forge.server.observability import agent_metrics

    a, v1 = _agent("beta")
    v2 = db.create_version(a["id"], system_prompt="p2", model="fake:hello")
    _seed_run(a, v1, dur=2)
    _seed_run(a, v1, status="timeout", error="turn limit of 30 reached", dur=10)
    _seed_run(a, v2, dur=1, tool_error=True)
    _seed_run(a, v2, source="eval", status="failed", error="model error: RuntimeError: boom")
    db.set_prod_version(a["id"], v2["id"], "human")
    a = db.get_agent(a["id"])
    m = agent_metrics(a, 14, include_eval=False)
    assert [p["version"] for p in m["promotions"]] == [1, 2] and m["promotions"][1]["from_version"] == 1
    assert m["prod_version"] == 2 and m["previous_prod_version"] == 1
    assert m["summary"]["runs"] == 3 and m["summary"]["success_rate"] == pytest.approx(2 / 3)
    # (100 * 1000 + 10 * 2000) / 1e6 = 0.12 USD per run
    assert m["summary"]["cost_usd"] == pytest.approx(0.36)
    rows = {r["version"]: r for r in m["versions"]}
    assert rows[2]["is_prod"] and rows[1]["is_previous_prod"]
    assert rows[1]["health"]["success_rate"] == 0.5 and rows[2]["health"]["success_rate"] == 1.0
    assert rows[2]["delta_vs_previous_prod"]["success_rate"]["change"] == pytest.approx(0.5)
    cats = {c["category"]: c for c in m["failures"]["all"]["categories"]}
    assert set(cats) == {"timeout", "tool_errors"}
    assert m["failures"]["all"]["reasons"][0]["reason"] in ("turn limit of 30 reached", "bash: exit 1, boom")
    bash = m["tools"]["tools"][0]
    assert bash["name"] == "bash" and bash["calls"] == 3 and bash["errors"] == 1 and bash["p50_ms"] == 200
    with_eval = agent_metrics(a, 14, include_eval=True)
    assert with_eval["summary"]["runs"] == 4
    assert "model_error" in {c["category"] for c in with_eval["failures"]["all"]["categories"]}


def test_overview_kpis_compare_with_previous_period(tmpdb):
    """OBS1: overview totals for the period, change versus the previous period, per agent rows and failure reasons."""
    from forge.server.observability import overview

    a, v = _agent("gamma")
    b, w = _agent("delta", model="anthropic:claude-mystery-9")
    now = time.time()
    _seed_run(a, v, created=now - 3 * 86400)  # in the previous 2 day period
    _seed_run(a, v, created=now - 3600)
    _seed_run(a, v, status="failed", error="model error: X", created=now - 1800)
    _seed_run(b, w, created=now - 600)
    o = overview(2, now=now)
    assert o["bucket"] == "hour" and o["kpis"]["runs"] == {"value": 3, "previous": 1, "change": 2, "change_pct": 2.0}
    rows = {r["slug"]: r for r in o["agents"]}
    assert rows["gamma"]["failing_runs"] == 1 and sum(rows["gamma"]["spark"]) == 2
    assert rows["delta"]["health"]["unpriced_runs"] == 1
    assert o["top_failure_reasons"][0]["agent"] == "gamma" and o["top_failing_agents"][0]["slug"] == "gamma"
    assert overview(7, now=now)["bucket"] == "day"


def test_repeated_eval_runs_failures_pass_rates_and_cache_tokens(tmpdb):
    """OBS4, OBS2, FR9: with k repeated eval runs, `eval_failed` counts each run whose own check failed (even when
    its case passed by majority) while pass rates use the majority verdict; cache tokens are reported, not priced."""
    from forge.server.observability import agent_metrics

    a, v = _agent("repeat")
    case = db.create_eval_case(a["id"], name="c", input="task", checker_type="shell", checker="true", split="train")
    runs = [_seed_run(a, v, source="eval") for _ in range(3)]
    for i, (r, ok) in enumerate(zip(runs, (True, False, True))):
        db.add_eval_run_result(v["id"], case["id"], i, r["id"], ok, "ok" if ok else "missing FLAKY.txt")
    db.upsert_eval_result(v["id"], case["id"], runs[0]["id"], True, "ok", passes=2, runs=3)
    db.update_run(runs[0]["id"], cache_read_tokens=800, cache_write_tokens=30)
    m = agent_metrics(db.get_agent(a["id"]), 14, include_eval=True)
    cats = {c["category"]: c for c in m["failures"]["all"]["categories"]}
    assert cats["eval_failed"]["runs"] == 1
    assert cats["eval_failed"]["example_run_ids"] == [runs[1]["id"]]
    assert m["versions"][0]["eval"]["train"]["passed"] == 1  # majority verdict: 2/3 passes
    assert m["summary"]["cache_read_tokens"] == 800 and m["summary"]["cache_write_tokens"] == 30
    assert m["summary"]["cost_usd"] == pytest.approx(3 * 0.12)  # cache tokens do not change cost
    assert m["cost"]["versions"][0]["cache_read_tokens"] == 800
