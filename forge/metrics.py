"""Observability aggregations: pure functions over database rows (no I/O), so each is unit-testable.

Rows are plain dicts shaped like the `runs`, `events` and `feedback` tables. The HTTP layer
(`forge/server/observability.py`) fetches rows with SQL and calls these functions.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Iterable

TERMINAL = ("succeeded", "failed", "timeout", "budget_exceeded")
ACTIVE = ("queued", "running", "awaiting_approval")  # awaiting_approval: paused on a reviewer, still holding a slot
HOUR, DAY = 3600, 86400

CATEGORY_LABELS = {
    "timeout": "Timeout",
    "budget_exceeded": "Spending cap reached",
    "model_error": "Model error",
    "sandbox_error": "Sandbox or executor error",
    "tool_errors": "Tool errors",
    "eval_failed": "Eval check failed",
    "negative_feedback": "Negative feedback",
}

# ---------------------------------------------------------------- basic statistics


def percentile(values: Iterable[float | None], q: float) -> float | None:
    """The q-th percentile (0 to 100) with linear interpolation between closest ranks; None when empty."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * max(0.0, min(100.0, q)) / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return float(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))


def ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def run_duration_ms(run: dict) -> float | None:
    """Execution time from start to end; None while unfinished or never started."""
    s, e = run.get("started_at"), run.get("ended_at")
    if s is None or e is None:
        return None
    return max(0.0, (e - s) * 1000)


def include_source(run: dict, include_eval: bool) -> bool:
    return include_eval or run.get("source") != "eval"


def first_line(text: str | None, limit: int = 160) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            line = line.strip()
            return line if len(line) <= limit else line[: limit - 3] + "..."
    return ""


# ---------------------------------------------------------------- health


def health(runs: list[dict], feedback: list[dict]) -> dict[str, Any]:
    """Production health of a set of runs.

    `runs` rows may carry a precomputed `cost_usd` (None for unknown prices). `feedback` rows are the
    feedback items on those runs (`rating` +1 or -1). Success rate is succeeded / finished runs;
    thumbs up rate is +1 items / rated items.
    """
    by_status: dict[str, int] = defaultdict(int)
    durations, turns = [], []
    tokens_in = tokens_out = cache_read = cache_write = 0
    cost, unknown = 0.0, 0
    for r in runs:
        by_status[r["status"]] += 1
        if r["status"] in TERMINAL:
            durations.append(run_duration_ms(r))
            turns.append(r.get("turns") or 0)
        tokens_in += r.get("tokens_in") or 0
        tokens_out += r.get("tokens_out") or 0
        cache_read += r.get("cache_read_tokens") or 0
        cache_write += r.get("cache_write_tokens") or 0
        c = r.get("cost_usd")
        if c is None and "cost_usd" in r:
            unknown += 1
        cost += c or 0.0
    finished = sum(by_status[s] for s in TERMINAL)
    up = sum(1 for f in feedback if (f.get("rating") or 0) > 0)
    down = sum(1 for f in feedback if (f.get("rating") or 0) < 0)
    return {
        "runs": len(runs),
        "finished": finished,
        "succeeded": by_status["succeeded"],
        "failed": by_status["failed"],
        "timeout": by_status["timeout"],
        "budget_exceeded": by_status["budget_exceeded"],
        "in_progress": sum(by_status[s] for s in ACTIVE),
        "awaiting_approval": by_status["awaiting_approval"],
        "success_rate": ratio(by_status["succeeded"], finished),
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
        "avg_turns": ratio(sum(turns), len(turns)),
        "feedback_count": up + down,
        "thumbs_up": up,
        "thumbs_down": down,
        "thumbs_up_rate": ratio(up, up + down),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read_tokens": cache_read,  # not priced, see forge.pricing.cost
        "cache_write_tokens": cache_write,
        "cost_usd": cost,
        "cost_per_run": ratio(cost, len(runs)),
        "unpriced_runs": unknown,
    }


def delta(current: float | None, previous: float | None) -> dict[str, float | None]:
    """Change of a metric versus a previous value (absolute and relative)."""
    change = current - previous if current is not None and previous is not None else None
    pct = change / abs(previous) if change is not None and previous else None
    return {"value": current, "previous": previous, "change": change, "change_pct": pct}


KPI_KEYS = ("runs", "success_rate", "p95_ms", "cost_usd", "thumbs_up_rate")


def kpis(cur: dict, prev: dict) -> dict[str, dict]:
    return {k: delta(cur.get(k), prev.get(k)) for k in KPI_KEYS}


# ---------------------------------------------------------------- time series


def bucket_size(days: float) -> int:
    """Hourly buckets for periods of two days or less, daily otherwise."""
    return HOUR if days <= 2 else DAY


def bucket_start(ts: float, size: int, tz_offset_s: int = 0) -> int:
    """Start of the bucket holding `ts`, aligned to local midnight given the client's UTC offset (seconds east)."""
    return int(math.floor((ts + tz_offset_s) / size) * size - tz_offset_s)


def series(runs: list[dict], since: float, until: float, size: int, tz_offset_s: int = 0) -> list[dict]:
    """Runs per bucket by status, spend and success rate, one entry per bucket from `since` to `until`."""
    first, last = bucket_start(since, size, tz_offset_s), bucket_start(until, size, tz_offset_s)
    out: dict[int, dict] = {}
    t = first
    while t <= last:
        out[t] = {"t": t, "runs": 0, "succeeded": 0, "failed": 0, "timeout": 0, "budget_exceeded": 0,
                  "in_progress": 0, "cost_usd": 0.0, "success_rate": None}
        t += size
    for r in runs:
        if not (since <= r["created_at"] <= until):
            continue
        b = out.get(bucket_start(r["created_at"], size, tz_offset_s))
        if b is None:
            continue
        b["runs"] += 1
        key = r["status"] if r["status"] in TERMINAL else "in_progress"
        b[key] += 1
        b["cost_usd"] += r.get("cost_usd") or 0.0
    for b in out.values():
        b["success_rate"] = ratio(b["succeeded"], sum(b[s] for s in TERMINAL))
    return list(out.values())


# ---------------------------------------------------------------- failures


def tool_error_reason(name: str, output: str | None) -> str:
    """A short reason for a failed tool call: for bash the first stderr (or stdout) line and exit code."""
    out = output or ""
    m = re.match(r"exit_code: (-?\d+)\n--- stdout ---\n(.*?)\n--- stderr ---\n(.*)", out, re.DOTALL)
    if m:
        detail = first_line(m.group(3)) or first_line(m.group(2))
        return f"{name}: exit {m.group(1)}" + (f", {detail}" if detail else "")
    return f"{name}: {first_line(out) or 'error'}"


def classify_run(run: dict, *, tool_errors: list[dict] | None = None, feedback: list[dict] | None = None,
                 eval_result: dict | None = None) -> list[dict]:
    """Failure categories for one run: [{category, reason, count}].

    - non-succeeded runs: `timeout`, `budget_exceeded` (per-run spending cap), `model_error` or
      `sandbox_error` (executor, runtime, interrupted)
    - any run with tool results flagged is_error: `tool_errors` (count = number of failed tool calls)
    - eval runs whose checker failed: `eval_failed` (reason = first line of the checker's reason)
    - runs with negative feedback: `negative_feedback` (reason = the comment)
    """
    out: list[dict] = []
    status, error = run.get("status"), run.get("error") or ""
    if status == "timeout":
        out.append({"category": "timeout", "reason": first_line(error) or "time limit reached", "count": 1})
    elif status == "budget_exceeded":
        out.append({"category": "budget_exceeded", "reason": first_line(error) or "spending cap exceeded", "count": 1})
    elif status == "failed":
        if error.startswith("model error"):
            out.append({"category": "model_error", "reason": first_line(error.removeprefix("model error: ")),
                        "count": 1})
        else:
            out.append({"category": "sandbox_error", "reason": first_line(error) or "unknown error", "count": 1})
    if tool_errors:
        first = tool_errors[0]
        out.append({"category": "tool_errors", "reason": tool_error_reason(first.get("name") or "tool",
                                                                           first.get("output")),
                    "count": len(tool_errors)})
    if eval_result is not None and not eval_result.get("passed"):
        out.append({"category": "eval_failed", "reason": first_line(eval_result.get("reason")) or "failed",
                    "count": 1})
    for f in feedback or []:
        if (f.get("rating") or 0) < 0:
            out.append({"category": "negative_feedback", "reason": first_line(f.get("comment")) or "(no comment)",
                        "count": 1})
    return out


def failure_breakdown(items: list[tuple[dict, list[dict]]], *, examples: int = 3, top: int = 10) -> dict:
    """Aggregates classified runs. `items` is [(run, classify_run(...))], runs newest first.

    Returns {"runs": failing run count, "categories": [...], "reasons": [...]}; each entry counts runs
    (`runs`) and occurrences (`count`, e.g. failed tool calls) with up to `examples` example run ids.
    """
    cats: dict[str, dict] = {}
    reasons: dict[tuple[str, str], dict] = {}
    failing = 0
    for run, found in items:
        if not found:
            continue
        failing += 1
        seen_cats: set[str] = set()
        for f in found:
            c = cats.setdefault(f["category"], {"category": f["category"], "label": CATEGORY_LABELS[f["category"]],
                                                "runs": 0, "count": 0, "example_run_ids": []})
            c["count"] += f["count"]
            if f["category"] not in seen_cats:
                seen_cats.add(f["category"])
                c["runs"] += 1
                if len(c["example_run_ids"]) < examples:
                    c["example_run_ids"].append(run["id"])
            key = (f["category"], f["reason"])
            r = reasons.setdefault(key, {"category": f["category"], "label": CATEGORY_LABELS[f["category"]],
                                         "reason": f["reason"], "runs": 0, "count": 0, "example_run_ids": []})
            r["count"] += f["count"]
            if run["id"] not in r["example_run_ids"]:
                r["runs"] += 1
                if len(r["example_run_ids"]) < examples:
                    r["example_run_ids"].append(run["id"])
    order = lambda x: (-x["runs"], -x["count"], x["category"], x.get("reason", ""))  # noqa: E731
    return {"runs": failing, "categories": sorted(cats.values(), key=order),
            "reasons": sorted(reasons.values(), key=order)[:top]}


# ---------------------------------------------------------------- tools


def tool_calls_from_events(events: list[dict]) -> list[dict]:
    """Pairs tool_call and tool_result events of one or more runs into calls.

    Each event dict has run_id, seq, ts, type and payload (or flattened `call_id`, `name`, `is_error`,
    `duration_ms`, `output`, `args`). Duration is the recorded `duration_ms`, falling back to the time
    between the call and result events for older traces.
    """
    pending: dict[tuple[str, str], dict] = {}
    calls: list[dict] = []
    for e in sorted(events, key=lambda e: (e["run_id"], e["seq"])):
        p = e.get("payload") or e
        cid = str(p.get("id") if "payload" in e else e.get("call_id"))
        key = (e["run_id"], cid)
        if e["type"] == "tool_call":
            pending[key] = e
        elif e["type"] == "tool_result":
            call = pending.pop(key, None)
            dur = p.get("duration_ms")
            if dur is None and call is not None:
                dur = max(0.0, (e["ts"] - call["ts"]) * 1000)
            args = ((call.get("payload") or call).get("args") if call else None) or {}
            calls.append({
                "run_id": e["run_id"], "seq": e["seq"], "ts": call["ts"] if call else e["ts"],
                "name": p.get("name") or "?", "is_error": bool(p.get("is_error")), "duration_ms": dur,
                "output": p.get("output"), "summary": args_summary(args), "version_n": e.get("version_n"),
            })
    return calls


def args_summary(args: Any, limit: int = 100) -> str:
    if not isinstance(args, dict):
        return ""
    text = args.get("cmd") or args.get("path") or ""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def tool_stats(calls: list[dict], *, slowest: int = 10) -> dict:
    """Per tool: calls, errors, error rate, p50 and p95 duration and total time; plus the slowest calls."""
    by_name: dict[str, list[dict]] = defaultdict(list)
    for c in calls:
        by_name[c["name"]].append(c)
    tools = []
    for name, cs in by_name.items():
        durs = [c["duration_ms"] for c in cs if c["duration_ms"] is not None]
        errors = sum(1 for c in cs if c["is_error"])
        tools.append({"name": name, "calls": len(cs), "errors": errors, "error_rate": ratio(errors, len(cs)),
                      "p50_ms": percentile(durs, 50), "p95_ms": percentile(durs, 95), "total_ms": sum(durs)})
    tools.sort(key=lambda t: (-t["total_ms"], t["name"]))
    slow = sorted((c for c in calls if c["duration_ms"] is not None), key=lambda c: -c["duration_ms"])[:slowest]
    return {"tools": tools,
            "slowest": [{k: c[k] for k in ("run_id", "seq", "name", "duration_ms", "is_error", "summary",
                                           "version_n")} for c in slow]}


# ---------------------------------------------------------------- timeline


def build_timeline(run: dict, events: list[dict]) -> dict:
    """One span per model turn and tool call of a run on a shared time axis (ms from run start).

    Uses the additive `duration_ms` / `model_duration_ms` payload fields when present. A tool_result's
    `approval_wait_ms` (governance) becomes its own `approval` span before the tool span. Older traces
    without them fall back to event timestamps: a model turn spans from the previous step's end to the
    event that reports it, a tool call from its tool_call to its tool_result event. Such spans are
    marked `estimated`.
    """
    evs = sorted(events, key=lambda e: e["seq"])
    started = next((e for e in evs if e["type"] == "run_started"), None)
    origin = started["ts"] if started else (run.get("started_at") or (evs[0]["ts"] if evs else 0.0))
    timed = any(("turn" in (e.get("payload") or {})) or ("duration_ms" in (e.get("payload") or {}))
                for e in evs if e["type"] in ("assistant", "tool_call", "tool_result"))
    spans: list[dict] = []
    calls: dict[str, dict] = {}
    cursor, prev_type = origin, "run_started"
    turn = 0
    end = None

    def ms(t: float) -> float:
        return round(max(0.0, (t - origin) * 1000), 1)

    def model_span(start: float, stop: float, ev: dict, estimated: bool, error: bool = False) -> None:
        nonlocal turn
        p = ev.get("payload") or {}
        turn = int(p.get("turn") or turn + 1)
        spans.append({"kind": "model", "label": "Model call failed" if error else f"Model turn {turn}",
                      "turn": turn, "seq": ev["seq"], "start_ms": ms(start),
                      "duration_ms": round(max(0.0, (stop - start) * 1000), 1),
                      "tokens_in": p.get("tokens_in"), "tokens_out": p.get("tokens_out"), "is_error": error,
                      "estimated": estimated})

    for e in evs:
        p, t, typ = e.get("payload") or {}, e["ts"], e["type"]
        if typ == "assistant":
            if timed and p.get("duration_ms") is not None:
                model_span(t - p["duration_ms"] / 1000, t, e, False)
            elif not timed:
                model_span(cursor, t, e, True)
        elif typ == "tool_call":
            if timed and p.get("model_duration_ms") is not None:
                model_span(t - p["model_duration_ms"] / 1000, t, e, False)
            elif not timed and prev_type in ("run_started", "tool_result"):
                model_span(cursor, t, e, True)
            calls[str(p.get("id"))] = e
        elif typ == "tool_result":
            call = calls.pop(str(p.get("id")), None)
            dur = p.get("duration_ms")
            start = t - dur / 1000 if dur is not None else (call["ts"] if call else cursor)
            wait = p.get("approval_wait_ms")
            if wait:  # governance: the human approval wait sits right before the tool's own execution
                spans.append({"kind": "approval", "label": f"Approval for {p.get('name') or 'tool'}", "turn": turn,
                              "seq": e["seq"], "start_ms": ms(start - wait / 1000), "duration_ms": round(wait, 1),
                              "is_error": False, "estimated": False})
            spans.append({"kind": "tool", "label": p.get("name") or "tool", "turn": turn, "seq": e["seq"],
                          "start_ms": ms(start), "duration_ms": round(dur if dur is not None
                                                                      else max(0.0, (t - start) * 1000), 1),
                          "is_error": bool(p.get("is_error")), "estimated": dur is None,
                          "summary": args_summary((call or {}).get("payload", {}).get("args"))})
        elif typ == "error":
            if p.get("duration_ms") is not None:
                model_span(t - p["duration_ms"] / 1000, t, e, False, error=True)
            elif str(p.get("message", "")).startswith("model error") and prev_type in ("run_started", "tool_result"):
                model_span(cursor, t, e, True, error=True)
        elif typ == "run_finished":
            end = t
        cursor, prev_type = t, typ
    if end is None:
        end = run.get("ended_at") or (evs[-1]["ts"] if evs else origin)
    total = ms(end)
    model_ms = round(sum(s["duration_ms"] for s in spans if s["kind"] == "model"), 1)
    tool_ms = round(sum(s["duration_ms"] for s in spans if s["kind"] == "tool"), 1)
    approval_ms = round(sum(s["duration_ms"] for s in spans if s["kind"] == "approval"), 1)
    created, run_start = run.get("created_at"), run.get("started_at")
    return {
        "run_id": run.get("id"),
        "status": run.get("status"),
        "estimated": not timed,
        "total_ms": max(total, max((s["start_ms"] + s["duration_ms"] for s in spans), default=0.0)),
        "model_ms": model_ms,
        "tool_ms": tool_ms,
        "approval_ms": approval_ms,  # time paused on human approvals, not counted as tool time
        "other_ms": round(max(0.0, total - model_ms - tool_ms - approval_ms), 1),
        "queue_ms": round(max(0.0, (run_start - created) * 1000), 1) if created and run_start else None,
        "turns": len([s for s in spans if s["kind"] == "model"]),
        "tool_calls": len([s for s in spans if s["kind"] == "tool"]),
        "spans": spans,
    }


# ---------------------------------------------------------------- prometheus

DURATION_BUCKETS = (1, 2.5, 5, 10, 30, 60, 120, 300, 600)


def _fmt(v: float) -> str:
    if v == math.inf:
        return "+Inf"
    if float(v).is_integer():
        return str(int(v))
    return repr(float(v))


def _labels(labels: dict[str, Any]) -> str:
    if not labels:
        return ""
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")  # noqa: E731
    return "{" + ",".join(f'{k}="{esc(v)}"' for k, v in labels.items()) + "}"


def histogram_samples(name: str, labels: dict[str, Any], values: list[float],
                      buckets: tuple[float, ...] = DURATION_BUCKETS) -> list[tuple[str, dict, float]]:
    out: list[tuple[str, dict, float]] = []
    for b in (*buckets, math.inf):
        out.append((f"{name}_bucket", {**labels, "le": _fmt(b)}, sum(1 for v in values if v <= b)))
    out.append((f"{name}_sum", labels, sum(values)))
    out.append((f"{name}_count", labels, len(values)))
    return out


def format_prometheus(families: list[dict]) -> str:
    """Prometheus text exposition format 0.0.4. Each family: {name, type, help, samples: [(name, labels, value)]}."""
    lines: list[str] = []
    for f in families:
        lines.append(f"# HELP {f['name']} {f['help']}")
        lines.append(f"# TYPE {f['name']} {f['type']}")
        for sample_name, labels, value in f["samples"]:
            lines.append(f"{sample_name}{_labels(labels)} {_fmt(value)}")
    return "\n".join(lines) + "\n"
