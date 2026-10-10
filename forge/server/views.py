"""Convert DB rows into the JSON objects defined in CONTRACT.md."""

from __future__ import annotations

from pathlib import Path

from forge import db, pricing

VERSION_KEYS = ("id", "agent_id", "n", "system_prompt", "lessons", "model", "tools", "http_tools", "network",
                "seed_files", "parent_id", "created_by", "rationale", "created_at")


def version_view(v: dict) -> dict:
    return {k: v[k] for k in VERSION_KEYS}


def agent_view(a: dict, *, versions: bool = False) -> dict:
    prod = db.get_version(a["prod_version_id"])
    out = {
        "id": a["id"], "slug": a["slug"], "name": a["name"], "description": a["description"],
        "prod_version": prod["n"] if prod else None, "auto_promote": a["auto_promote"],
        "share_token": a["share_token"], "created_at": a["created_at"],
    }
    from forge.server.evals import eval_repeats  # lazy: evals -> runs -> views

    out["eval_repeats"] = eval_repeats(a)
    if versions:
        out["versions"] = [version_view(v) for v in db.list_versions(a["id"])]
    return out


def run_view(r: dict, *, version_n: int | None = None, model: str | None = None) -> dict:
    if version_n is None or model is None:
        v = db.get_version(r["version_id"])
        version_n = v["n"] if v else version_n
        model = v["model"] if v else model
    price = pricing.price_for(model)
    return {
        "id": r["id"], "agent_id": r["agent_id"], "version_id": r["version_id"], "version_n": version_n,
        "source": r["source"], "input": r["input"], "status": r["status"], "output": r["output"],
        "error": r["error"], "turns": r["turns"], "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"],
        "cache_read_tokens": r.get("cache_read_tokens") or 0, "cache_write_tokens": r.get("cache_write_tokens") or 0,
        "has_artifacts": bool(r["artifact_path"] and Path(r["artifact_path"]).exists()),
        "eval_case_id": r["eval_case_id"],
        "created_at": r["created_at"], "started_at": r["started_at"], "ended_at": r["ended_at"],
        "feedback": [feedback_view(f) for f in db.list_feedback(r["id"])],
        # Additive observability fields (CONTRACT.md "Observability").
        "duration_ms": round((r["ended_at"] - r["started_at"]) * 1000, 1) if r["ended_at"] and r["started_at"] else None,
        "cost_usd": pricing.cost(model, r["tokens_in"], r["tokens_out"]),
        "cost_note": price.note,
    }


def feedback_view(f: dict) -> dict:
    return {k: f[k] for k in ("id", "run_id", "rating", "comment", "source", "created_at")}


def event_view(e: dict) -> dict:
    return {k: e[k] for k in ("run_id", "seq", "ts", "type", "payload")}


def eval_case_view(c: dict) -> dict:
    return {k: c[k] for k in ("id", "agent_id", "name", "input", "seed_files", "checker_type", "checker", "split",
                              "created_at")}


def improvement_view(im: dict) -> dict:
    s = im["summary"] or {}
    base = db.get_version(im["base_version_id"])
    cand = db.get_version(im["candidate_version_id"])
    empty = {"train": {"passed": 0, "total": 0}, "holdout": {"passed": 0, "total": 0}}
    return {
        "id": im["id"], "agent_id": im["agent_id"], "status": im["status"],
        "base_version": base["n"] if base else None, "candidate_version": cand["n"] if cand else None,
        "base": s.get("base", empty), "candidate": s.get("candidate", empty), "cases": s.get("cases", []),
        "newly_passing": s.get("newly_passing", []), "regressions": s.get("regressions", []),
        "lessons_diff": s.get("lessons_diff", []), "error": s.get("error"),
        "created_at": im["created_at"], "ended_at": im["ended_at"],
    }
